"""Code-quality reviewer: bugs, bad patterns, naming, complexity."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import context_block, get_llm, repo_context_block


def run_code_quality_agent(diff: str, instructions: str = "", repo_context: str = "") -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff.
Analyze the code quality: look for bugs, bad patterns, naming issues, and complexity.
Be concise and specific. Return 3-5 bullet points. Be specific, reference file names. If clean, say '✓ No major issues found.'
{context_block(instructions)}{repo_context_block(repo_context)}PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
