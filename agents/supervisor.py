"""Supervisor: synthesizes the three reviewers' notes into one PR verdict."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.llm import context_block, get_llm


def run_supervisor(
    code_feedback: str, test_feedback: str, docs_feedback: str, instructions: str = ""
) -> str:
    prompt = f"""You are a tech lead. Synthesize the feedback below into one clear PR review.
Start with a one-sentence verdict prefixed with one of: **Approve**, **Request Changes**,
or **Needs Discussion**. Then summarize the key points grouped under short headings.
Be direct and constructive. Drop redundant or low-value notes.
{context_block(instructions)}
Code Quality Notes:
{code_feedback}

Test Coverage Notes:
{test_feedback}

Documentation Notes:
{docs_feedback}
"""
    return get_llm().invoke([HumanMessage(content=prompt)]).content
