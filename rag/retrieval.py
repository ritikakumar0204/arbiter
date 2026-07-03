"""Public RAG façade for the review pipeline.

`build_repo_context()` is the one function the rest of Arbiter calls. It lazily
indexes the repo, retrieves the chunks most relevant to the PR diff, and returns
a ready-to-embed context block. It NEVER raises: on a disabled/misconfigured/
unavailable RAG layer it returns "", so reviews fall back to diff-only exactly
as before.
"""

from __future__ import annotations

import logging

from github.Repository import Repository

from rag import config, embeddings, indexer, store

log = logging.getLogger("arbiter.rag.retrieval")

# Per-excerpt cap so a few large chunks can't blow up the reviewer prompt.
_EXCERPT_MAX_CHARS = 800


def _format(hits: list[dict]) -> str:
    if not hits:
        return ""
    blocks = []
    for h in hits:
        excerpt = h["content"].strip()
        if len(excerpt) > _EXCERPT_MAX_CHARS:
            excerpt = excerpt[:_EXCERPT_MAX_CHARS] + "\n…[excerpt truncated]…"
        blocks.append(f"--- {h['path']} ---\n{excerpt}")
    body = "\n\n".join(blocks)
    return (
        "Related excerpts retrieved from elsewhere in the repository "
        "(NOT part of this PR's diff). Use them to judge correctness, spot "
        "duplication of existing helpers, and check consistency with existing "
        "patterns:\n\n" + body
    )


def build_repo_context(
    repo_obj: Repository,
    repo_full_name: str,
    diff: str,
    changed_paths: list[str],
) -> str:
    """Return a repo-context block for the reviewers, or "" if RAG is unavailable."""
    if not config.is_enabled():
        return ""
    if not diff.strip():
        return ""
    try:
        indexer.ensure_indexed(repo_obj, repo_full_name)
        query = diff[: config.RAG_QUERY_MAX_CHARS]
        qvec = embeddings.embed_query(query)
        hits = store.query(repo_full_name, qvec, exclude_paths=changed_paths)
        context = _format(hits)
        log.info("Retrieved %d repo chunks for %s", len(hits), repo_full_name)
        return context
    except Exception:  # noqa: BLE001 — RAG is best-effort; degrade to diff-only
        log.exception("RAG retrieval failed for %s; continuing without context", repo_full_name)
        return ""
