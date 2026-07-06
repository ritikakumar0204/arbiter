"""FastAPI server: receives GitHub PR webhooks and posts an AI review.

Flow:
  GitHub  ──(pull_request webhook, HMAC-signed)──▶  POST /webhook
                                                     │  verify signature
                                                     │  filter to relevant actions
                                                     │  202 Accepted (fast ACK)
                                                     ▼
                                          background task:
                                            auth as installation
                                            fetch PR files + diff
                                            run_review(pr_context)
                                            post comment back to PR

Run locally:
    uvicorn main:app --reload --port 8000
Then expose it to GitHub with a tunnel (ngrok/cloudflared) and point the App's
webhook URL at https://<tunnel>/webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

import github_utils
from graph.review_graph import PRContext, format_comment, review_diff

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arbiter")


def _init_rag() -> None:
    """Best-effort pgvector schema init. Any failure downgrades to diff-only reviews.

    RAG is optional: without DATABASE_URL (or if psycopg/pgvector aren't installed)
    Arbiter reviews PRs exactly as before, so nothing here is allowed to crash boot.
    """
    try:
        from rag import config
        if not config.is_enabled():
            log.info("RAG disabled (no DATABASE_URL) — reviews run diff-only")
            return
        from rag.db import init_schema
        init_schema()
        log.info("RAG enabled — repo-aware reviews via pgvector")
    except Exception:  # noqa: BLE001 — optional feature; never block startup
        log.exception("RAG init failed; continuing diff-only")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_rag()
    yield


app = FastAPI(title="Arbiter PR Review Agent", lifespan=lifespan)

# Landing page (served at /). Absolute path so it resolves regardless of cwd.
INDEX_HTML = Path(__file__).parent / "static" / "index.html"

# PR actions worth reviewing. "opened"/"reopened" = new PR; "synchronize" = new push.
REVIEWABLE_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def verify_signature(body: bytes, signature_header: str | None) -> None:
    """Validate GitHub's X-Hub-Signature-256 HMAC. Raises 401 on mismatch."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(500, "GITHUB_WEBHOOK_SECRET is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(401, "Missing or malformed signature header")

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(401, "Signature verification failed")


def _repo_context(pr, repo: str, files: list[dict], diff: str) -> str:
    """RAG-retrieved repo excerpts for the diff, or "" when RAG is unavailable."""
    try:
        from rag import config
        if not config.is_enabled():
            return ""
        from rag import retrieval
        changed_paths = [f["filename"] for f in files]
        return retrieval.build_repo_context(pr.base.repo, repo, diff, changed_paths)
    except Exception:  # noqa: BLE001 — best-effort; degrade to diff-only
        log.exception("RAG context build failed for %s#%s", repo, pr.number)
        return ""


def _sync_linear(pr, repo: str, verdict: str) -> None:
    """Mirror the verdict onto the PR's linked Linear issue, if configured."""
    try:
        from integrations import linear
        if not linear.is_enabled():
            return
        linear.sync_review(pr, repo, verdict)
    except Exception:  # noqa: BLE001 — best-effort; GitHub review already posted
        log.exception("Linear sync failed for %s#%s", repo, pr.number)


def process_pull_request(installation_id: int, repo: str, number: int) -> None:
    """Background worker: review one PR and post the result."""
    try:
        pr = github_utils.get_pull_request(installation_id, repo, number)
        files = github_utils.collect_pr_files(pr)
        diff = github_utils.build_diff_text(files)
        context: PRContext = {
            "repo": repo,
            "number": number,
            "title": pr.title or "",
            "body": pr.body or "",
            "files": files,
            "diff": diff,
            "instructions": github_utils.fetch_review_instructions(pr),
            "repo_context": _repo_context(pr, repo, files, diff),
        }
        verdict = review_diff(diff, context["instructions"], context["repo_context"])
        github_utils.post_review_comment(pr, format_comment(context, verdict))
        log.info("Posted review on %s#%s", repo, number)
        if diff.strip():
            _sync_linear(pr, repo, verdict)
    except Exception:  # noqa: BLE001 — log and swallow; webhook already ACKed
        log.exception("Failed to process %s#%s", repo, number)


@app.get("/")
def home() -> FileResponse:
    """Portfolio landing page describing Arbiter and linking to the GitHub App."""
    return FileResponse(INDEX_HTML)


@app.get("/logo.png")
def logo() -> FileResponse:
    """Serve the repo-root logo so the landing page's relative <img> resolves both
    when served (/) and when the HTML file is opened directly (file://)."""
    return FileResponse(Path(__file__).parent / "logo.png")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    verify_signature(body, x_hub_signature_256)
    payload = await request.json()

    if x_github_event == "ping":
        return {"msg": "pong"}

    if x_github_event != "pull_request":
        return {"msg": f"ignored event: {x_github_event}"}

    action = payload.get("action")
    if action not in REVIEWABLE_ACTIONS:
        return {"msg": f"ignored action: {action}"}

    installation_id = payload["installation"]["id"]
    repo = payload["repository"]["full_name"]
    number = payload["pull_request"]["number"]

    # Hand off to the background so GitHub gets a fast 202 (webhooks time out ~10s).
    background_tasks.add_task(process_pull_request, installation_id, repo, number)
    log.info("Queued review for %s#%s (action=%s)", repo, number, action)
    return {"msg": "review queued", "repo": repo, "number": number}
