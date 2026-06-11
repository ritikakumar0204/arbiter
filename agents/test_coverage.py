"""Test-coverage reviewer: missing or weak tests for the changed code."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import get_llm


def run_test_coverage_agent(diff: str) -> str:
    prompt = f"""You are a senior engineer reviewing a pull request diff for test coverage.
Identify changed logic that lacks tests, edge cases that aren't covered, and any
tests that look brittle or missing assertions. Suggest concrete test cases to add.
Be concise and specific. Return bullet points only.
If coverage looks adequate, say so in one bullet.

PR Diff:
{diff}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
