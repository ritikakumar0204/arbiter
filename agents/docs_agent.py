"""Documentation reviewer: missing docstrings, comments, and unclear naming."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import context_block, get_llm


def run_docs_agent(diff: str, instructions: str = "") -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff for documentation.
Look for public functions/classes missing docstrings, complex logic lacking comments,
outdated comments, and unclear naming that needs explanation. Return 3-5 bullet points. Be specific. If sufficient, say "✓ Documentation looks good."
{context_block(instructions)}
PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
