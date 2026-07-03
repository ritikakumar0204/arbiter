"""Postgres + pgvector access: schema init and a lazily-opened connection pool.

Design notes:
  * `init_schema()` runs once at startup on a standalone connection so the
    `vector` extension exists *before* the pool opens. The pool's per-connection
    `configure` hook then registers the pgvector type adapters, which requires
    the extension to already be present.
  * Everything here raises on failure. Callers in the RAG layer are expected to
    catch and degrade to diff-only reviews — the DB is an enhancement, not a
    hard dependency.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from rag import config

log = logging.getLogger("arbiter.rag.db")

# {dim} is substituted at init; the column type is then fixed for the DB's life.
_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repo_index (
    repo        TEXT PRIMARY KEY,
    head_sha    TEXT NOT NULL,
    file_count  INT  NOT NULL DEFAULT 0,
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repo_chunks (
    id          BIGSERIAL PRIMARY KEY,
    repo        TEXT NOT NULL,
    path        TEXT NOT NULL,
    blob_sha    TEXT NOT NULL,
    chunk_index INT  NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector({dim}) NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS repo_chunks_repo_path_idx ON repo_chunks (repo, path);
CREATE INDEX IF NOT EXISTS repo_chunks_embedding_idx
    ON repo_chunks USING hnsw (embedding vector_cosine_ops);
"""


def init_schema() -> None:
    """Create the extension, tables, and indexes. Idempotent; safe to call at boot."""
    url = config.database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_SCHEMA.format(dim=config.EMBEDDING_DIM))
    log.info("RAG schema ready (embedding dim=%d)", config.EMBEDDING_DIM)


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    url = config.database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # register_vector needs the extension present — init_schema() runs first.
    return ConnectionPool(
        url,
        min_size=1,
        max_size=int(os.getenv("RAG_POOL_MAX", "4")),
        configure=register_vector,
        open=True,
    )


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection with pgvector adapters registered."""
    with _pool().connection() as conn:
        yield conn
