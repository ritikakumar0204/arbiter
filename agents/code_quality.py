"""Code-quality reviewer: bugs, bad patterns, naming, complexity."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import get_llm


def run_code_quality_agent(diff: str) -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff.
Analyze the code quality: look for bugs, bad patterns, naming issues, and complexity.
Be concise and specific. Reference file names where relevant. Return bullet points only.
If the diff looks clean, say so in one bullet.

PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
