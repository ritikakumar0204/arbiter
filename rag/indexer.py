"""Index a repository's default branch into pgvector, incrementally.

Strategy: walk the git tree of the default branch's HEAD. Each blob carries a
git SHA that *is* a content hash, so we only re-embed files whose SHA changed
since the last index, delete files that disappeared, and skip everything else.
An unchanged repo (same HEAD SHA) is a single cheap lookup with no API calls.

All work is bounded (RAG_MAX_FILES, RAG_MAX_FILE_BYTES) to keep webhook-time
indexing within GitHub API and latency budgets.
"""

from __future__ import annotations

import base64
import binascii
import logging

from github.Repository import Repository

from rag import chunker, config, embeddings, store

log = logging.getLogger("arbiter.rag.indexer")


def _decode_blob(repo_obj: Repository, blob_sha: str) -> str | None:
    """Fetch a git blob and decode it as UTF-8 text, or None if binary/unreadable."""
    blob = repo_obj.get_git_blob(blob_sha)
    if blob.encoding != "base64":
        return None
    try:
        raw = base64.b64decode(blob.content)
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _eligible_blobs(repo_obj: Repository, tree_sha: str) -> dict[str, str]:
    """Return {path: blob_sha} for indexable files on the tree, capped to the budget."""
    tree = repo_obj.get_git_tree(tree_sha, recursive=True)
    if getattr(tree, "raw_data", {}).get("truncated"):
        log.warning("Git tree truncated by GitHub — indexing a partial file set")

    eligible: dict[str, str] = {}
    for entry in tree.tree:
        if entry.type != "blob":
            continue
        size = entry.size or 0
        if chunker.is_indexable(entry.path, size):
            eligible[entry.path] = entry.sha

    if len(eligible) > config.RAG_MAX_FILES:
        kept = dict(sorted(eligible.items())[: config.RAG_MAX_FILES])
        log.warning(
            "Repo has %d indexable files; capping to RAG_MAX_FILES=%d",
            len(eligible), config.RAG_MAX_FILES,
        )
        return kept
    return eligible


def ensure_indexed(repo_obj: Repository, repo_full_name: str) -> dict:
    """Bring the pgvector index for `repo_full_name` up to date with its HEAD.

    Returns a small status dict for logging: {status, upserted, deleted, files}.
    """
    branch = repo_obj.get_branch(repo_obj.default_branch)
    head_sha = branch.commit.sha
    tree_sha = branch.commit.commit.tree.sha

    state = store.get_index_state(repo_full_name)
    if state and state["head_sha"] == head_sha:
        return {"status": "fresh", "upserted": 0, "deleted": 0, "files": state["file_count"]}

    eligible = _eligible_blobs(repo_obj, tree_sha)
    existing = store.existing_blob_shas(repo_full_name)

    to_upsert = [p for p, sha in eligible.items() if existing.get(p) != sha]
    to_delete = [p for p in existing if p not in eligible]

    upserted = 0
    for path in to_upsert:
        content = _decode_blob(repo_obj, eligible[path])
        if content is None:
            continue
        chunks = chunker.chunk_text(content)
        if not chunks:
            continue
        vectors = embeddings.embed_documents(chunks)
        store.replace_file_chunks(repo_full_name, path, eligible[path], chunks, vectors)
        upserted += 1

    store.delete_paths(repo_full_name, to_delete)
    store.set_index_state(repo_full_name, head_sha, len(eligible))

    log.info(
        "Indexed %s @ %s: +%d files, -%d files (%d eligible)",
        repo_full_name, head_sha[:7], upserted, len(to_delete), len(eligible),
    )
    return {
        "status": "updated" if state else "created",
        "upserted": upserted,
        "deleted": len(to_delete),
        "files": len(eligible),
    }
