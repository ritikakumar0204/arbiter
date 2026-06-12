# 🤖 Arbiter — Multi-Agent AI Code Reviewer

> A GitHub App that automatically reviews every pull request with a team of specialized AI agents and posts a single, synthesized verdict back as a comment.
<img alt="Python" src=logo.png>

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white">
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-multi--agent-1C3C3C">
  <img alt="Gemini" src="https://img.shields.io/badge/Gemini-2.5%20Flash-4285F4?logo=googlegemini&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-deployable-2496ED?logo=docker&logoColor=white">
</p>

---

## What it does

Open or update a pull request, and Arbiter reviews it within seconds — no human in the loop. Instead of one generalist model, it runs **three specialist reviewers in parallel** (code quality, test coverage, documentation) and a **supervisor** that merges their notes into one clear verdict: **Approve**, **Request Changes**, or **Needs Discussion**.

> _Add a screenshot of a posted review comment here — it's the strongest single thing you can show._
>
> `![Example review](docs/example-review.png)`

## Architecture

```
                                          ┌─ code_quality ─┐
  GitHub PR ──webhook──▶  FastAPI  ──────▶ ┼─ test_coverage ─┼──▶ supervisor ──▶ PR comment
  (signed)              (verify + queue)   └─ docs ─────────┘     (synthesize)
                              │                    LangGraph
                              └─ 202 ACK (fast)    (parallel fan-out / fan-in)
```

1. GitHub sends a signed `pull_request` webhook.
2. The server **verifies the HMAC signature**, then queues the work and returns immediately — GitHub gets its ACK well inside the ~10s webhook timeout.
3. In the background, the app authenticates as a **GitHub App installation**, fetches the PR diff, and runs the **LangGraph**: the three reviewers execute concurrently, the supervisor waits for all of them, then writes the verdict.
4. The verdict is posted back as a PR comment.

## Engineering highlights

- **Multi-agent orchestration with LangGraph** — true parallel fan-out/fan-in, not sequential model calls. Each agent owns its own slice of state, so there are no write conflicts.
- **Secure webhooks** — every payload is validated with an HMAC-SHA256 signature check (`hmac.compare_digest`) before any work happens; forged requests are rejected with a 401.
- **Non-blocking by design** — reviews run in a background task so the webhook ACKs fast and never trips GitHub's delivery timeout.
- **Proper GitHub App auth** — short-lived JWT → per-installation access token (PyGithub 2.x), scoped to exactly the repos the App is installed on.
- **Swappable model & clean seams** — the entire agent brain sits behind one function, `run_review(pr_context) -> markdown`; the model is a single env var (`GEMINI_MODEL`).
- **Deploy-ready** — containerized, with a one-file Render blueprint; the same Dockerfile runs on Fly.io, Railway, or Cloud Run.

## Tech stack

| Layer | Choice |
|---|---|
| Agent orchestration | **LangGraph** |
| LLM | **Google Gemini** (`gemini-2.5-flash-lite`) via `langchain-google-genai` |
| Web server | **FastAPI** + Uvicorn |
| GitHub integration | **GitHub App** + PyGithub |
| Deploy | **Docker** + Render |

## Project structure

```
arbiter/
├── main.py              FastAPI server: /webhook, signature check, background dispatch
├── github_utils.py      GitHub App auth, PR diff fetch, comment posting
├── graph/
│   └── review_graph.py  LangGraph wiring + run_review() entry point
├── agents/
│   ├── llm.py           shared Gemini client
│   ├── code_quality.py  ┐
│   ├── test_coverage.py ├─ specialist reviewers
│   ├── docs_agent.py    ┘
│   └── supervisor.py    synthesizes the final verdict
├── Dockerfile
└── render.yaml          one-click deploy blueprint
```

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # then fill it in (see below)

uvicorn main:app --reload --port 8000
```

Because GitHub can't reach `localhost`, expose the server with a tunnel and point the App's webhook at it:

```bash
ngrok http 8000
# webhook URL → https://<tunnel-host>/webhook
```

Health check: `GET /health` → `{"status":"ok"}`.

### Configuration (`.env`)

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key |
| `GEMINI_MODEL` | Model id (default `gemini-2.5-flash-lite`) |
| `GITHUB_APP_ID` | From the App's settings page |
| `GITHUB_WEBHOOK_SECRET` | Must match the secret set on the App |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the App's `.pem` (local), **or** |
| `GITHUB_PRIVATE_KEY` | The PEM contents inline (used in deploys) |

### Create the GitHub App

GitHub → Settings → Developer settings → **GitHub Apps → New**:

- **Webhook URL**: your public URL + `/webhook` · **Webhook secret**: a long random string
- **Permissions**: Pull requests *Read & write*, Contents *Read-only*
- **Subscribe to events**: *Pull request*
- Generate a **private key** (`.pem`), copy the **App ID**, then **install** the App on a repo.

## Deploy (Render)

The repo ships a [Dockerfile](Dockerfile) and a [render.yaml](render.yaml) blueprint.

1. Push the repo to GitHub.
2. [render.com](https://render.com) → **New + → Blueprint** → select the repo (Render reads `render.yaml`).
3. Set the secret env vars in the dashboard: `GEMINI_API_KEY`, `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_PRIVATE_KEY` (paste the full `.pem` — there's no key file on the host).
4. Point the App's webhook at `https://<your-service>.onrender.com/webhook`.

> **Note:** Render's free tier sleeps after ~15 min idle, so the first request after a nap takes a few seconds to wake. GitHub retries, so reviews still post. For always-warm hosting, the same Docker image runs on Fly.io, Railway, or Google Cloud Run.

## Possible extensions

- Inline review comments on specific diff lines (not just a summary)
- Per-repo config (`.arbiter.yml`) to tune which agents run
- Caching to skip re-review of unchanged files on `synchronize`
- A web dashboard of past reviews
