"""Linear integration: mirror a PR review onto its linked Linear issue.

When a reviewed PR maps to a Linear issue, Arbiter posts the verdict as a comment
on that issue and (optionally) tags it with an `arbiter:<verdict>` label — so the
review shows up where the work item lives, not just in GitHub.

Design (same contract as the RAG layer): entirely optional and best-effort.
`is_enabled()` is False without LINEAR_API_KEY, and `sync_review()` swallows every
error, so a Linear outage or a PR with no linked issue never affects the GitHub
review that already posted.

Issue resolution:
  1. Linear's `issueVcsBranchSearch` maps the PR's head branch to its issue — this
     works out of the box when the repo is connected to the Linear workspace.
  2. Fallback: scan the branch/title/body for an identifier like `ARB-1`.
"""

from __future__ import annotations

import logging
import os
import re

import httpx

log = logging.getLogger("arbiter.integrations.linear")

API_URL = "https://api.linear.app/graphql"
_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9]{1,9}-\d+")

# Verdict → (label slug, label color hex). Order matters: check the stronger
# signals before "approve" so a "cannot approve / request changes" reads correctly.
_VERDICT_RULES = (
    ("request changes", ("changes-requested", "#e5484d")),
    ("needs discussion", ("needs-discussion", "#f5a623")),
    ("approve", ("approved", "#4cb782")),
)
_DEFAULT_VERDICT = ("reviewed", "#8a8f98")

_ISSUE_FIELDS = "id identifier url team { id } labels { nodes { id name } }"


def _api_key() -> str | None:
    return os.getenv("LINEAR_API_KEY") or None


def is_enabled() -> bool:
    if os.getenv("LINEAR_ENABLED", "").lower() in {"0", "false", "no"}:
        return False
    return _api_key() is not None


def _apply_labels() -> bool:
    return os.getenv("LINEAR_APPLY_LABELS", "true").lower() not in {"0", "false", "no"}


def _gql(query: str, variables: dict) -> dict:
    """POST a GraphQL request; raise on transport or GraphQL errors."""
    resp = httpx.post(
        API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": _api_key(), "Content-Type": "application/json"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Linear GraphQL error: {data['errors']}")
    return data["data"]


def classify_verdict(verdict: str) -> tuple[str, str]:
    """Map the supervisor's verdict text to a (label slug, color) pair."""
    head = verdict[:300].lower()
    for needle, result in _VERDICT_RULES:
        if needle in head:
            return result
    return _DEFAULT_VERDICT


def _find_issue(branch: str, title: str, body: str) -> dict | None:
    """Resolve the Linear issue for a PR, or None if it can't be linked."""
    # 1) Branch → issue (works when the repo is connected to the workspace).
    if branch:
        try:
            data = _gql(
                f"query($b:String!){{ issueVcsBranchSearch(branchName:$b){{ {_ISSUE_FIELDS} }} }}",
                {"b": branch},
            )
            node = data.get("issueVcsBranchSearch")
            if node:
                return node
        except Exception:  # noqa: BLE001 — fall through to the identifier scan
            log.debug("issueVcsBranchSearch failed for %s", branch, exc_info=True)

    # 2) Fallback: find an identifier (ARB-1) in branch/title/body and look it up.
    for text in (branch, title, body):
        if not text:
            continue
        match = _IDENTIFIER_RE.search(text.upper())
        if not match:
            continue
        try:
            data = _gql(
                f"query($q:String!){{ issueSearch(query:$q){{ nodes {{ {_ISSUE_FIELDS} identifier }} }} }}",
                {"q": match.group(0)},
            )
            for node in data.get("issueSearch", {}).get("nodes", []):
                if node.get("identifier") == match.group(0):
                    return node
        except Exception:  # noqa: BLE001
            log.debug("issueSearch failed for %s", match.group(0), exc_info=True)
    return None


def _comment_body(repo: str, number: int, url: str, verdict: str) -> str:
    return f"🤖 **Arbiter** reviewed [{repo}#{number}]({url})\n\n{verdict}"


def _post_comment(issue_id: str, body: str) -> None:
    _gql(
        "mutation($id:String!,$body:String!){ commentCreate(input:{issueId:$id, body:$body}){ success } }",
        {"id": issue_id, "body": body},
    )


def _label_id(team_id: str, name: str, color: str) -> str | None:
    """Find a workspace label by name, creating it on the issue's team if absent."""
    found = _gql(
        "query($n:String!){ issueLabels(filter:{name:{eq:$n}}){ nodes { id } } }",
        {"n": name},
    )
    nodes = found.get("issueLabels", {}).get("nodes", [])
    if nodes:
        return nodes[0]["id"]
    created = _gql(
        """mutation($n:String!,$c:String!,$t:String!){
             issueLabelCreate(input:{name:$n, color:$c, teamId:$t}){ issueLabel { id } }
           }""",
        {"n": name, "c": color, "t": team_id},
    )
    return created.get("issueLabelCreate", {}).get("issueLabel", {}).get("id")


def _apply_verdict_label(issue: dict, verdict: str) -> None:
    """Add an `arbiter:<verdict>` label to the issue without dropping existing ones."""
    team = issue.get("team") or {}
    team_id = team.get("id")
    if not team_id:
        return
    slug, color = classify_verdict(verdict)
    label_id = _label_id(team_id, f"arbiter:{slug}", color)
    if not label_id:
        return
    nodes = issue.get("labels", {}).get("nodes", [])
    current_ids = [n["id"] for n in nodes]
    # Keep non-Arbiter labels; drop any prior arbiter:* verdict label so the issue
    # reflects only the latest verdict (e.g. changes-requested → approved on re-review).
    kept = [n["id"] for n in nodes if not str(n.get("name", "")).startswith("arbiter:")]
    new_ids = kept + [label_id]
    if set(new_ids) == set(current_ids):
        return
    _gql(
        "mutation($id:String!,$ids:[String!]){ issueUpdate(id:$id, input:{labelIds:$ids}){ success } }",
        {"id": issue["id"], "ids": new_ids},
    )


def sync_review(pr, repo: str, verdict: str) -> str | None:
    """Post the review onto the PR's linked Linear issue. Returns the issue id or None.

    `pr` is a PyGithub PullRequest (for head branch, title, body, number, url).
    Never raises — logs and returns None on any failure.
    """
    if not is_enabled():
        return None
    try:
        issue = _find_issue(pr.head.ref, pr.title or "", pr.body or "")
        if not issue:
            log.info("No linked Linear issue for branch %s — skipping", pr.head.ref)
            return None

        _post_comment(issue["id"], _comment_body(repo, pr.number, pr.html_url, verdict))
        if _apply_labels():
            _apply_verdict_label(issue, verdict)

        log.info("Synced review to Linear issue %s", issue.get("identifier"))
        return issue.get("identifier")
    except Exception:  # noqa: BLE001 — best-effort; the GitHub review already posted
        log.exception("Linear sync failed for %s#%s; continuing", repo, pr.number)
        return None
