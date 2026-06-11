# Arbiter — AI PR Review Agent

A GitHub App that listens for pull-request events, runs review agents on the
diff, and posts a verdict back as a comment.

The full pipeline is wired: the FastAPI webhook server, signature verification,
GitHub App auth, diff fetching, the **LangGraph agent brain** (three reviewers +
supervisor), and comment posting.

## Layout

| File | Role |
|------|------|
| [main.py](main.py) | FastAPI server, `/webhook` endpoint, HMAC check, background dispatch |
| [github_utils.py](github_utils.py) | App auth, fetch PR files/diff, post comment |
| [graph/review_graph.py](graph/review_graph.py) | LangGraph: fan out to agents → supervisor → `run_review(pr_context) -> markdown` |
| [agents/](agents/) | `code_quality`, `test_coverage`, `docs` reviewers + `supervisor`; shared Gemini client in `llm.py` |
| [.env.example](.env.example) | Config template — copy to `.env` |

## The agent graph

```
        ┌─ code_quality ─┐
START ──┼─ test_coverage ─┼──▶ supervisor ──▶ comment
        └─ docs ─────────┘
```

The three reviewers run in parallel over the PR diff; the supervisor waits for
all three, then writes a verdict (**Approve** / **Request Changes** /
**Needs Discussion**) with grouped key points. Model defaults to
`gemini-2.0-flash` — override with `GEMINI_MODEL` in `.env`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then fill it in
```

### Create the GitHub App
1. GitHub → Settings → Developer settings → **GitHub Apps** → New.
2. **Webhook URL**: your public tunnel URL + `/webhook` (see below).
   **Webhook secret**: a long random string → put it in `GITHUB_WEBHOOK_SECRET`.
3. **Permissions**: Pull requests = *Read & write*, Contents = *Read-only*.
4. **Subscribe to events**: *Pull request*.
5. Generate a **private key** (.pem), download it, and either point
   `GITHUB_PRIVATE_KEY_PATH` at it or paste it into `GITHUB_PRIVATE_KEY`.
6. Copy the **App ID** into `GITHUB_APP_ID`, then **Install** the App on a repo.

## Run locally

```bash
uvicorn main:app --reload --port 8000
```

Expose it so GitHub can reach it (webhooks need a public URL):

```bash
ngrok http 8000
# or: cloudflared tunnel --url http://localhost:8000
```

Set the App's webhook URL to `https://<tunnel-host>/webhook`. Open or push to a
PR on the installed repo — Arbiter posts a comment within a few seconds.

- Health check: `GET /health` → `{"status":"ok"}`
- GitHub's initial "ping" event returns `{"msg":"pong"}`.

## How it works

1. GitHub POSTs a signed `pull_request` webhook to `/webhook`.
2. The server verifies the `X-Hub-Signature-256` HMAC against the secret.
3. Relevant actions (`opened`, `reopened`, `synchronize`, `ready_for_review`)
   are queued to a background task; GitHub gets an immediate ACK.
4. The worker mints an installation token, fetches the PR's changed files and
   diff, calls `run_review`, and posts the result as a PR comment.

## Phase 5 — Deploy (Render)

Hosting gives you a permanent HTTPS URL, so you can stop running ngrok + uvicorn
locally. Files: [Dockerfile](Dockerfile), [.dockerignore](.dockerignore),
[render.yaml](render.yaml).

**Key difference from local:** there is no `.pem` file on the host. Instead of
`GITHUB_PRIVATE_KEY_PATH`, set the **`GITHUB_PRIVATE_KEY`** env var to the full
PEM contents. `github_utils._load_private_key()` falls back to it automatically.

1. **Push to GitHub** (Render deploys from a repo):
   ```bash
   git add -A && git commit -m "Arbiter PR review agent"
   gh repo create arbiter --private --source=. --push
   ```
2. **Create the service**: [render.com](https://render.com) → **New + → Blueprint**
   → pick the `arbiter` repo. Render reads `render.yaml`.
3. **Set the secret env vars** in the Render dashboard (they're `sync:false`):
   - `GEMINI_API_KEY`
   - `GITHUB_APP_ID`
   - `GITHUB_WEBHOOK_SECRET` (same value as in your App)
   - `GITHUB_PRIVATE_KEY` → open your `.pem` in a text editor, copy **everything**
     including the `-----BEGIN/END-----` lines, paste into the field.
4. **Deploy.** When live, Render gives you `https://arbiter-pr-review.onrender.com`.
   Confirm `https://<that-host>/health` returns `{"status":"ok"}`.
5. **Repoint the App webhook**: GitHub App → Edit → **Webhook URL** →
   `https://<that-host>/webhook` → Save. Redeliver a `ping` to confirm 200.

⚠️ Render's **free** tier sleeps after ~15 min idle; the first webhook after a
sleep can take ~50s to wake, which may exceed GitHub's delivery timeout. GitHub
retries, so the review still posts, but for snappier behavior use a paid instance
or a host that stays warm (Fly.io, Railway). The Dockerfile works on all of them.
