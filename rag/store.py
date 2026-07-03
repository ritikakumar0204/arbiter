"""Data access for the pgvector store: index bookkeeping, upserts, and search.

Vector parameters are passed as pgvector string literals cast with `::vector`
rather than relying on driver-level list adaptation, so this works the same
across pgvector-python versions and needs no numpy.
"""

from __future__ import annotations

import logging

from rag import config
from rag.db import connection

log = logging.getLogger("arbiter.rag.store")


def _vec_literal(values: list[float]) -> str:
    """Render an embedding as a pgvector literal: [0.1,0.2,...]."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def get_index_state(repo: str) -> dict | None:
    """Return {'head_sha', 'file_count'} for a repo, or None if never indexed."""
    with connection() as conn:
        row = conn.execute(
            "SELECT head_sha, file_count FROM repo_index WHERE repo = %s",
            (repo,),
        ).fetchone()
    if not row:
        return None
    return {"head_sha": row[0], "file_count": row[1]}


def set_index_state(repo: str, head_sha: str, file_count: int) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO repo_index (repo, head_sha, file_count, indexed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (repo) DO UPDATE
              SET head_sha = EXCLUDED.head_sha,
                  file_count = EXCLUDED.file_count,
                  indexed_at = now()
            """,
            (repo, head_sha, file_count),
        )
        conn.commit()


def existing_blob_shas(repo: str) -> dict[str, str]:
    """Map path -> blob_sha for everything currently stored (for incremental diff)."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT path, blob_sha FROM repo_chunks WHERE repo = %s",
            (repo,),
        ).fetchall()
    return {path: blob_sha for path, blob_sha in rows}


def replace_file_chunks(
    repo: str,
    path: str,
    blob_sha: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> None:
    """Atomically replace all stored chunks for one file."""
    rows = [
        (repo, path, blob_sha, i, content, _vec_literal(vec))
        for i, (content, vec) in enumerate(zip(chunks, embeddings))
    ]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM repo_chunks WHERE repo = %s AND path = %s", (repo, path))
            if rows:
                cur.executemany(
                    """
                    INSERT INTO repo_chunks
                        (repo, path, blob_sha, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    """,
                    rows,
                )
        conn.commit()


def delete_paths(repo: str, paths: list[str]) -> None:
    """Remove chunks for files no longer present in the repo."""
    if not paths:
        return
    with connection() as conn:
        conn.execute(
            "DELETE FROM repo_chunks WHERE repo = %s AND path = ANY(%s)",
            (repo, paths),
        )
        conn.commit()


def query(
    repo: str,
    query_embedding: list[float],
    top_k: int | None = None,
    exclude_paths: list[str] | None = None,
) -> list[dict]:
    """Return the top-K most similar chunks: [{path, content, distance}, ...].

    `exclude_paths` drops files already present in the PR diff — the agents see
    those verbatim, so re-surfacing them as "context" wastes the budget.
    """
    top_k = top_k or config.RAG_TOP_K
    vec = _vec_literal(query_embedding)

    exclude_clause = ""
    params: list = [vec, repo]
    if exclude_paths:
        exclude_clause = "AND NOT (path = ANY(%s))"
        params.append(list(exclude_paths))
    params += [vec, top_k]

    sql = f"""
        SELECT path, content, embedding <=> %s::vector AS distance
        FROM repo_chunks
        WHERE repo = %s {exclude_clause}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"path": p, "content": c, "distance": float(d)} for p, c, d in rows]
