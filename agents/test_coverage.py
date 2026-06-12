"""Test-coverage reviewer: missing or weak tests for the changed code."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import context_block, get_llm


def run_test_coverage_agent(diff: str, instructions: str = "") -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff for test coverage.
Identify changed logic that lacks tests, edge cases that aren't covered, and any
tests that look brittle or missing assertions. Suggest concrete test cases to add.
Be concise and specific. Return 3-5 bullet points. If adequate, say "✓ Coverage looks sufficient."
{context_block(instructions)}
PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
