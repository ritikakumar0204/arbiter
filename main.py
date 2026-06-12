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

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

import github_utils
from graph.review_graph import PRContext, run_review

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arbiter")

app = FastAPI(title="Arbiter PR Review Agent")

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


def process_pull_request(installation_id: int, repo: str, number: int) -> None:
    """Background worker: review one PR and post the result."""
    try:
        pr = github_utils.get_pull_request(installation_id, repo, number)
        files = github_utils.collect_pr_files(pr)
        context: PRContext = {
            "repo": repo,
            "number": number,
            "title": pr.title or "",
            "body": pr.body or "",
            "files": files,
            "diff": github_utils.build_diff_text(files),
            "instructions": github_utils.fetch_review_instructions(pr),
        }
        review = run_review(context)
        github_utils.post_review_comment(pr, review)
        log.info("Posted review on %s#%s", repo, number)
    except Exception:  # noqa: BLE001 — log and swallow; webhook already ACKed
        log.exception("Failed to process %s#%s", repo, number)


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
