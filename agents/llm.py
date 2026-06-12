"""Shared Gemini client for all agents.

Centralized so the model name and API key are configured in one place. The
guide stores the key as GEMINI_API_KEY; ChatGoogleGenerativeAI looks for
GOOGLE_API_KEY by default, so we pass it through explicitly.

GEMINI_MODEL overrides the default (gemini-1.5-flash was deprecated in 2025 —
default to a current flash model, override via .env if needed).
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

DEFAULT_MODEL = "gemini-2.0-flash"


@lru_cache(maxsize=1)
def get_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        google_api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.2,
    )


def context_block(instructions: str) -> str:
    """Render maintainer-provided review instructions as a prompt preamble.

    Sourced from the repo's optional .arbiter.yml. Returns "" when there are no
    instructions, so prompts are unchanged in the default (no-config) case.
    """
    instructions = (instructions or "").strip()
    if not instructions:
        return ""
    return (
        "The repository maintainers provided these review instructions. "
        "Follow them where they apply:\n"
        f'"""\n{instructions}\n"""\n'
    )
