"""RAG configuration, sourced from the environment.

The whole RAG layer is *optional*. If DATABASE_URL is unset, `is_enabled()`
returns False and every RAG entry point becomes a no-op — Arbiter reviews PRs
exactly as it did before (diff-only, stateless). This keeps the zero-infra
deploy working and lets pgvector be a strict upgrade rather than a requirement.
"""

from __future__ import annotations

import os


def database_url() -> str | None:
    """Postgres connection string, e.g. postgresql://user:pass@host:5432/db."""
    return os.getenv("DATABASE_URL") or None


def is_enabled() -> bool:
    """RAG is on only when a database is configured and not explicitly disabled."""
    if os.getenv("RAG_ENABLED", "").lower() in {"0", "false", "no"}:
        return False
    return database_url() is not None


# --- Embeddings ---
# text-embedding-004 outputs 768-dim vectors; keep EMBEDDING_DIM in sync if you
# switch models (the DB column type is fixed at init time to this dimension).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBEDDING_BATCH = int(os.getenv("EMBEDDING_BATCH", "100"))

# --- Retrieval ---
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
# Diff is truncated before embedding — the query embedding has a token limit
# (~2048 tokens for text-embedding-004) and the diff is often far larger.
RAG_QUERY_MAX_CHARS = int(os.getenv("RAG_QUERY_MAX_CHARS", "6000"))

# --- Indexing bounds (keep webhook-time indexing cheap and API-budget-safe) ---
RAG_MAX_FILES = int(os.getenv("RAG_MAX_FILES", "600"))
RAG_MAX_FILE_BYTES = int(os.getenv("RAG_MAX_FILE_BYTES", "100000"))
CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "1500"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
