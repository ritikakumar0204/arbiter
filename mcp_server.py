"""MCP server exposing Arbiter's multi-agent reviewer to any MCP client.

This is a second, independent surface onto the same agent brain that powers the
GitHub App (main.py) — but it needs **no webhook, no GitHub App credentials, and
no database**. The only configuration is a Gemini API key and (optionally) a
model name. Point Claude Desktop, Cursor, or any MCP client at it and review any
diff you have locally.

Run (stdio transport):
    python mcp_server.py

Environment:
    GEMINI_API_KEY   required — Google AI Studio key
    GEMINI_MODEL     optional — defaults to the value in agents/llm.py

Example Claude Desktop config (claude_desktop_config.json):
    {
      "mcpServers": {
        "arbiter": {
          "command": "python",
          "args": ["mcp_server.py"],
          "cwd": "/absolute/path/to/arbiter",
          "env": {
            "GEMINI_API_KEY": "your_key",
            "GEMINI_MODEL": "gemini-2.5-flash-lite"
          }
        }
      }
    }
"""

from __future__ import annotations

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env before the agents build their LLM client (get_llm reads env lazily).
load_dotenv()

from agents.code_quality import run_code_quality_agent
from agents.docs_agent import run_docs_agent
from agents.test_coverage import run_test_coverage_agent
from graph.review_graph import review_diff

mcp = FastMCP("arbiter")


@mcp.tool()
def review_pull_request(diff: str, instructions: str = "") -> str:
    """Full multi-agent review of a code diff.

    Runs three specialist reviewers (code quality, test coverage, documentation)
    in parallel and returns the supervisor's synthesized verdict, which starts
    with one of **Approve**, **Request Changes**, or **Needs Discussion**.

    Args:
        diff: A unified diff, e.g. the output of `git diff` or `git diff main`.
        instructions: Optional maintainer guidance to steer the review.
    """
    return review_diff(diff, instructions)


@mcp.tool()
def review_code_quality(diff: str, instructions: str = "") -> str:
    """Review a diff for bugs, bad patterns, naming issues, and complexity only."""
    return run_code_quality_agent(diff, instructions)


@mcp.tool()
def review_test_coverage(diff: str, instructions: str = "") -> str:
    """Review a diff for missing tests, uncovered edge cases, and weak assertions."""
    return run_test_coverage_agent(diff, instructions)


@mcp.tool()
def review_documentation(diff: str, instructions: str = "") -> str:
    """Review a diff for missing docstrings, unclear naming, and stale comments."""
    return run_docs_agent(diff, instructions)


if __name__ == "__main__":
    mcp.run()
