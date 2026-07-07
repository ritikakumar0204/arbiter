# Arbiter

> **A GitHub App that reviews every pull request with a team of specialist AI agents — grounded in your actual codebase, customizable per repo, exposed over MCP, and synced to Linear.**

This document is written against the evaluation rubric. Each section maps to a scoring category so it's easy to follow. For the full technical README (setup, architecture, deploy), see **[README.md](https://github.com/ritikakumar0204/arbiter/blob/main/README.md)**.

| | |
|---|---|
| **Live landing page** | served at [Arbiter](https://arbiter-pr-review.onrender.com/) |
| **Source** | https://github.com/ritikakumar0204/arbiter |
| **Video walkthrough** | [Demo](https://drive.google.com/file/d/11cibtoLxFueaHZONck4MeQahnxAjset7/view?usp=sharing) |
| **Stack** | LangGraph · Google Gemini · FastAPI · pgvector · MCP · Linear API · Docker |

---

## 1. Problem Framing & Real-World Impact

### The pain point
Code review is the biggest source of latency in a shipping team's workflow. A PR sits waiting for a human who is busy, in another timezone, or out. When the review finally lands, a large share of it is *mechanical*: "this is missing a test," "no docstring," "you wrote `=` instead of `==`," "this duplicates a helper we already have." That work is real and necessary — but it burns senior-engineer attention that should go to architecture and product decisions, and it stalls the author for hours or days.

### Who is affected
Small, fast-moving engineering teams — the exact profile of an early-stage SaaS/B2B/PropTech company — where there aren't enough senior reviewers to keep up with PR volume, and where a slow review loop directly slows the roadmap.

### Why AI is the right tool (and why *this* shape of AI)
The mechanical layer of review is pattern-matching over code and diffs — precisely what LLMs are good at. But a naive "summarize this diff" bot is shallow: it can't tell you a change duplicates an existing helper or violates an established pattern, because it only sees the diff. The right tool is an LLM **grounded in the repository** (RAG) and **decomposed into specialists** (multi-agent), so each concern is reviewed deliberately rather than in one generic pass.

### Scope (deliberately narrow)
Arbiter does **one thing well**: it posts a single, actionable verdict — **Approve / Request Changes / Needs Discussion** — on every PR, within seconds of it opening. It is *not* trying to replace human review; it's trying to remove the mechanical first pass so humans review higher-level things.

### What success looks like & how to quantify it
- **Time-to-first-review**: from hours/days → **seconds** (four parallel Gemini calls complete in well under the webhook window).
- **Cost per review**: on Gemini `2.5-flash-lite`, a full four-agent review is a **fraction of a cent** — cheap enough to run on *every* PR, not a sampled subset.
- **Reviewer load**: measurable as the share of PRs where the human reviewer adds *no* new mechanical comments beyond Arbiter's — i.e. Arbiter caught them first.
- **Catch rate**: bugs / missing tests / undocumented public APIs flagged before a human looks. In live testing it caught an `=`/`==` assignment bug, missing edge-case tests, and missing docstrings on a sample PR.
- **Loop closure**: with the Linear integration, the verdict reaches the *work item*, so a PM or lead sees "changes requested" without opening GitHub.

---

## 2. Technical Execution

### Architecture
```
GitHub PR ──webhook (HMAC-signed)──▶ FastAPI ──▶ retrieve repo context (pgvector/RAG)
                                       │                     │
                                       │            ┌─ code_quality ─┐
                                       │            ┼─ test_coverage ─┼──▶ supervisor ──▶ verdict
                                       │            └─ documentation ─┘      │
                                       │                LangGraph            ├─▶ GitHub PR comment
                                       └─ fast ACK      (parallel)           └─▶ Linear issue (comment + label)
```

### Module structure — clear separation of concerns
| Area | Files | Responsibility |
|---|---|---|
| Web / webhook | [main.py](main.py) | Signature verification, background dispatch, orchestration |
| GitHub | [github_utils.py](github_utils.py) | App auth (JWT → installation token), diff fetch, comment posting, `.arbiter.yml` config |
| Agent brain | [graph/review_graph.py](graph/review_graph.py), [agents/](agents/) | LangGraph wiring + specialist reviewers + supervisor; injects repo instructions + retrieved context |
| RAG | [rag/](rag/) | Config, pgvector store, embeddings, chunker, indexer, retrieval |
| MCP | [mcp_server.py](mcp_server.py) | The reviewers exposed as MCP tools |
| Integrations | [integrations/linear.py](integrations/linear.py) | Verdict → Linear issue comment + verdict label (swaps to reflect the latest verdict) |

### Key design decisions & tradeoffs

- **True parallel multi-agent, not sequential calls.** The three reviewers fan out from `START` in one LangGraph superstep and each writes to a **disjoint state key**, so there are zero concurrent-write conflicts and the supervisor runs only after all three finish. Tradeoff: slightly more orchestration code than a for-loop, for a real latency win and clean separation.

- **Stateless core + optional subsystems that degrade gracefully.** RAG, MCP, and Linear are each **strictly optional**. With no `DATABASE_URL`, no `LINEAR_API_KEY`, or those packages absent, Arbiter reviews diff-only exactly as before — the heavy imports are lazy and gated, and every integration is wrapped so a failure can never break the review that already posted. This was a deliberate principle: *add capability without making the base deploy fragile.*

- **pgvector over a standalone vector DB.** Chosen because the roadmap (a review-history dashboard) needs relational data anyway, so one database serves both vectors and metadata with hybrid `WHERE` + similarity queries — instead of operating two datastores. It's also the stronger production story. Tradeoff: more setup friction (needs Postgres locally, via the provided [docker-compose.yml](docker-compose.yml)) than an in-process store.

- **Incremental indexing keyed on git blob SHAs.** A blob's git SHA *is* its content hash, so re-indexing only re-embeds files that actually changed; an unchanged repo is a single cheap lookup with zero embedding calls. All indexing is bounded (`RAG_MAX_FILES`, file-size caps) to stay within API/latency budgets.

- **One shared review core across surfaces.** `review_diff()` is transport-agnostic; the GitHub App wraps it in a PR-comment envelope, the MCP server returns it verbatim, and the Linear sync reuses the *same* verdict from a single graph run (no double-billing the LLM).

- **Per-repo custom reviewers via `.arbiter.yml`.** A repo can ship an `.arbiter.yml` (read from the PR's base branch) whose `instructions` are injected into every agent prompt — so each repository shapes its own reviewer (house rules, stack conventions, strictness) with **no code change and zero infrastructure**. Reviews that used it are tagged `· applied repo .arbiter.yml` on the PR comment. This is the primary way non-technical users tune Arbiter's behavior.

- **Verdict labels reflect the *latest* review, not history.** On Linear, applying a new verdict label removes any prior `arbiter:*` label, so an issue never shows contradictory `changes-requested` + `approved` at once — it swaps as the PR evolves through re-reviews.

### Error handling & reproducibility
- HMAC-SHA256 webhook verification with `hmac.compare_digest`; forged requests get a 401 before any work.
- Broad best-effort boundaries around every optional subsystem, with logging.
- Containerized ([Dockerfile](Dockerfile)) with a one-file Render blueprint ([render.yaml](render.yaml)) provisioning web + Postgres; local pgvector via `docker compose up -d`. Setup is documented in the README and works from a clean checkout.

### Testing — what's verified vs. what's pending (honest)
- **Verified live:** the full multi-agent review against Gemini (caught a real bug); `.arbiter.yml` steering the review (same diff reviewed differently with vs. without house rules, and a full **Request Changes → fix → Approve** arc, repeated to confirm it's stable); the MCP server over a real stdio client roundtrip; and the Linear sync against a real workspace — comment posted *and the verdict label confirmed to swap* (`changes-requested` → `approved`) by reading the issue back.
- **Verified offline:** config gating, chunker filtering, verdict classification, graceful-degradation paths, all route serving.
- **Pending a real smoke test:** the pgvector DB path (needs Docker, which wasn't available on the build machine) and the single fully-integrated GitHub→review→Linear webhook pass. Every *link* in that chain is individually proven; they haven't been exercised as one run. This is called out rather than hidden.

---

## 3. AI Usage

### AI *in* the product (non-trivial orchestration)
- **Agentic multi-agent pattern** — specialists + supervisor over LangGraph with parallel fan-out/fan-in.
- **RAG** — repository embedded into pgvector; each diff drives cosine retrieval (HNSW) of the most relevant code, injected as grounding context.
- **Tool use / interop** — the reviewers are exposed as **MCP tools**, so any MCP client (Claude Desktop, Cursor) can drive them; the same brain also runs as a webhook service. AI is a force multiplier at the *product* level, not a single prompt.
- **Prompt steerability by config** — per-repo `.arbiter.yml` instructions are injected into the agent prompts at review time, so the *same* pipeline enforces different house rules (money-in-cents, reuse-this-helper, docstring requirements, strictness) per repository without any code change.

### AI in *how I built it* (dev-level)
This project was built with an AI coding agent (Claude Code) as a pair, and that changed the process concretely:
- **Speed of scaffolding.** Whole subsystems (the `rag/` package, the MCP server, the Linear client) went from design to working code in one sitting each, because I could describe the architecture and iterate on the generated modules instead of typing boilerplate.
- **Design conversations, not just codegen.** The Chroma-vs-pgvector decision, the graceful-degradation principle, and the "share one `review_diff` core across surfaces" refactor came out of back-and-forth reasoning about tradeoffs — the AI surfaced options and I made the calls.

### Where it hit limits, and how I adapted
- **It couldn't test what the environment couldn't run.** No Docker on the machine meant the pgvector path stayed unit-verified, not integration-tested. Adaptation: verify all the pure logic offline and *explicitly document* the pending smoke test rather than claim it passed.
- **A plausible design had an observability blind spot.** The Linear sync degraded *silently* when disabled — so when a live review didn't post to Linear, there was no log explaining why. That ambiguity cost real debugging time. Lesson: "best-effort and silent" is wrong; best-effort should still be *observable*.
- **A test artifact caused confusion.** The Linear half was first tested with a hardcoded fake verdict; comparing that comment to a real GitHub review made them look inconsistent until I traced it back to the synthetic input. Lesson: make test fixtures obviously synthetic, and prefer end-to-end runs when comparing surfaces.
- **Generated code needs review like any code.** A stray corrupted CSS value and an absolute image path that broke `file://` both shipped in a first draft and were caught on inspection — a reminder that AI output is a starting point to verify, not to trust blindly.
- **Reviewer strictness is itself a tuning problem.** Prompting the agents to "be strict, never approve unless perfect" made the reviewer *unable to approve anything* — the test-coverage agent kept inventing "add one more test" demands even for complete code. Too lenient, and it waved a real bug through. Getting `.arbiter.yml` to approve clean code while still blocking a genuine behavioral bug took real iteration and verifying both ends live — a concrete lesson in how sensitive multi-agent behavior is to instruction wording.
- **Committed ≠ deployed.** A verdict-label fix worked locally but not in the live demo — because the commit hadn't been *pushed*, so the deployed reviewer was running stale code. An obvious-in-hindsight reminder that "it's committed" and "it's what the server runs" are different claims.


---

## Conclusion 
The base project was supposed to be a code reviewer. Arbiter ships five coherent capabilities on top of that — repo-aware RAG, per-repo custom reviewers via `.arbiter.yml`, an MCP server, a Linear integration, and a redesigned landing page — chosen not as feature-checklist padding but as one narrative arc: *retrieve context → reason as specialists (shaped by the repo's own house rules) → deliver the verdict wherever the team works.*
