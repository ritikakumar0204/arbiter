"""Documentation reviewer: missing docstrings, comments, and unclear naming."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import get_llm


def run_docs_agent(diff: str) -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff for documentation.
Look for public functions/classes missing docstrings, complex logic lacking comments,
outdated comments, and unclear naming that needs explanation. Be concise and specific.
Return bullet points only. If documentation looks sufficient, say so in one bullet.

PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
