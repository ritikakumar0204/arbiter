"""Decide which repo files to index, and split them into embeddable chunks.

Chunking is intentionally simple: fixed-size character windows with overlap so a
symbol near a boundary still appears whole in one chunk. Code-aware (AST) chunking
would be better but adds language parsers; this is a solid, language-agnostic start.
"""

from __future__ import annotations

from rag import config

# Directories we never index (dependencies, build output, VCS internals).
SKIP_DIR_PARTS = {
    ".git", "node_modules", "vendor", "dist", "build", "out", "target",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", "coverage", ".mypy_cache",
}

# Binary / non-source extensions — embedding these is noise.
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
    ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
    ".mp3", ".wav", ".bin", ".exe", ".dll", ".so", ".dylib", ".class",
    ".jar", ".pyc", ".lock", ".pem", ".key",
}

# Generated / vendored files that add bulk without review value.
SKIP_SUFFIXES = (".min.js", ".min.css", "-lock.json", ".lock")
SKIP_FILENAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}


def is_indexable(path: str, size: int) -> bool:
    """True if a repo blob at `path` (bytes `size`) is worth embedding."""
    if size <= 0 or size > config.RAG_MAX_FILE_BYTES:
        return False
    parts = path.replace("\\", "/").split("/")
    if any(part in SKIP_DIR_PARTS for part in parts):
        return False
    name = parts[-1]
    if name in SKIP_FILENAMES or name.endswith(SKIP_SUFFIXES):
        return False
    lower = name.lower()
    if any(lower.endswith(ext) for ext in BINARY_EXTS):
        return False
    return True


def chunk_text(content: str) -> list[str]:
    """Split file content into overlapping character windows.

    Windows extend to the next newline where possible so chunks end on a line
    boundary rather than mid-token. Empty/whitespace chunks are dropped.
    """
    size = config.CHUNK_CHARS
    overlap = config.CHUNK_OVERLAP
    if len(content) <= size:
        return [content] if content.strip() else []

    chunks: list[str] = []
    start = 0
    n = len(content)
    while start < n:
        end = min(start + size, n)
        # Nudge the cut to the next newline (within a small look-ahead) for cleaner breaks.
        if end < n:
            nl = content.find("\n", end, end + 200)
            if nl != -1:
                end = nl + 1
        piece = content[start:end]
        if piece.strip():
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks
