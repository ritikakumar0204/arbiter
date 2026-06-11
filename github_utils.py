"""GitHub App helpers: authentication, fetching PR contents, and posting reviews.

A GitHub *App* authenticates in two steps:
  1. Sign a short-lived JWT with the App's private key to prove "I am this App".
  2. Exchange that for an *installation access token* scoped to the one
     org/repo where the App is installed. All repo API calls use that token.

PyGithub handles both for us via Auth.AppAuth + GithubIntegration.
"""

from __future__ import annotations

import os
from functools import lru_cache

from github import Auth, GithubIntegration
from github.PullRequest import PullRequest


def _load_private_key() -> str:
    """Read the App private key from a .pem path or an inline env var."""
    path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    inline = os.getenv("GITHUB_PRIVATE_KEY")
    if inline:
        # Allow keys pasted as a single line with literal "\n" separators.
        return inline.replace("\\n", "\n")

    raise RuntimeError(
        "No GitHub private key found. Set GITHUB_PRIVATE_KEY_PATH to your .pem "
        "file or paste the PEM into GITHUB_PRIVATE_KEY."
    )


@lru_cache(maxsize=1)
def _integration() -> GithubIntegration:
    """A cached GithubIntegration (the App-level client)."""
    app_id = os.environ["GITHUB_APP_ID"]
    auth = Auth.AppAuth(int(app_id), _load_private_key())
    return GithubIntegration(auth=auth)


def get_pull_request(installation_id: int, repo_full_name: str, pr_number: int) -> PullRequest:
    """Return a PullRequest object using a token scoped to this installation.

    `repo_full_name` is "owner/repo"; `installation_id` comes from the webhook
    payload (`payload["installation"]["id"]`).
    """
    gh = _integration().get_github_for_installation(installation_id)
    repo = gh.get_repo(repo_full_name)
    return repo.get_pull(pr_number)


def collect_pr_files(pr: PullRequest) -> list[dict]:
    """Flatten a PR's changed files into dicts the agents can consume.

    Each entry: {filename, status, additions, deletions, patch}. `patch` is the
    unified diff for that file (may be None for binary/very large files).
    """
    files = []
    for f in pr.get_files():
        files.append(
            {
                "filename": f.filename,
                "status": f.status,  # added | modified | removed | renamed
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": getattr(f, "patch", None),
            }
        )
    return files


def build_diff_text(files: list[dict], max_chars: int = 60_000) -> str:
    """Concatenate per-file patches into one diff string, truncated for the LLM."""
    chunks = []
    for f in files:
        if not f.get("patch"):
            continue
        header = f"### {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})"
        chunks.append(f"{header}\n{f['patch']}")
    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…[diff truncated]…"
    return text


def post_review_comment(pr: PullRequest, body: str) -> None:
    """Post the supervisor's verdict as a single issue comment on the PR."""
    pr.create_issue_comment(body)
