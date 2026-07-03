"""Gemini embeddings for indexing (documents) and retrieval (queries).

We keep two clients because the embedding model produces better-aligned vectors
when told the task: `retrieval_document` for stored code chunks and
`retrieval_query` for the diff we search with. Both use the same GEMINI_API_KEY
already configured for the reviewer LLM.
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_google_genai import GoogleGenerativeAIEmbeddings

from rag import config


@lru_cache(maxsize=2)
def _client(task_type: str) -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=config.EMBEDDING_MODEL,
        google_api_key=os.getenv("GEMINI_API_KEY"),
        task_type=task_type,
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed code chunks for storage, batching to stay under per-request limits."""
    client = _client("retrieval_document")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), config.EMBEDDING_BATCH):
        batch = texts[start : start + config.EMBEDDING_BATCH]
        vectors.extend(client.embed_documents(batch))
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single search query (the PR diff)."""
    return _client("retrieval_query").embed_query(text)
