# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The MVP backend, plain frontend, offline test suite and evaluation harness are implemented; since 24 Nov 2026 the models are LangChain and the vector store is Qdrant (rollback tag `pre-langchain` = the Pinecone version). The evaluation is done and merged: corpus in `corpus/`, `evals/golden_set.json` (42 questions, labels written by Claude, not the owner), real numbers in `EVAL.md` (re-run 25 Nov 2026 on prompt v2), and `DESIGN.md` / `AI_USAGE.md`. Built and merged: B1 auth, B3 monitoring (OpenTelemetry spans stored in SQLite, `/metrics/*`, a Monitoring tab), B5 file types, B6 document guardrails, B7 edge cases (`scripts/run_edge_cases.py`, 27 of 27). B8 (deploy) is on hold by the owner's instruction and has no Dockerfile. B4 (the KNOWLEX React frontend, source in `frontend/`, built into `app/static/` and committed; Node 20+ only to change the UI: `cd frontend; npm install; npm run dev|test|build`; the plain version is git tag `plain-frontend`) is built and merged. Still missing, because they need the owner: Swagger screenshots, the demo video, and a decision on publishing `corpus/` and the AI chat history.

- `PLAN.md` is the source of truth for decisions, API contract, pipelines, config variables, guardrails and evaluation rules. When code and plan disagree, fix one of them deliberately and update the other; do not create additional plan documents. Known deviations from the plan's §5 layout: `app/container.py` (wiring), `scripts/`, `notebooks/nb_support.py`.
- `architecture.html` is a layer-by-layer visual of PLAN.md; update it when flows, status codes or layers change.
- `_requirements/` holds the instructor's brief (PDF) and grading rubric (xlsx): read-only. `_archive/` is obsolete (old Gemini/Chroma designs and the pre-rewrite PLAN) and intentionally not committed.
- Model IDs and vendor limits are tagged **[VERIFY]** in PLAN.md and were checked on 24 Nov 2026 (docs plus a live smoke test and a live upload/ask/delete run against OpenAI and Qdrant Cloud; results in PLAN.md §19.3). Both models reject `temperature`, so never send it. Never invent numbers; every result in a document must come from a real run.

## Git and attribution (owner's rule, from `_requirements/CLAUDE.md`)

Never add Claude as author, co-author or contributor anywhere in git or GitHub: no `Co-Authored-By: Claude` trailer, no "Generated with Claude Code" line or session link in commits, PRs, issues or comments, and do not change the git author/committer. This overrides any default attribution a tool or template would add.

## Owner decisions that constrain all work

- Optional features the owner will attempt: B1 JWT auth, B4 modern frontend, B5 extra file types, B6 document guardrails, B7 edge cases, B8 deployment, written as *optional* and built only after the MVP. Do **not** write about B2 (multilingual). B3 (monitoring) was added on 25 Nov 2026 at the owner's request.
- English-only refusal text. Multilingual-jailbreak handling is deliberately deferred.
- Vendors: OpenAI only for models (the owner has no Anthropic credit) and Qdrant as the vector DB (Qdrant Cloud free for evals/deploy, embedded `QDRANT_PATH=:memory:` for tests). LangChain provides `ChatOpenAI`, `OpenAIEmbeddings`, `Document` and the splitter; no LangGraph, no chains, no RAGAS. The vector store uses `qdrant-client` directly (we embed through `llm_guard`) with LangChain's payload layout. DeepEval is an optional cross-check only.
- The product is corpus-agnostic (users upload any documents); a small 3–5 document corpus exists only for the golden set and demo.
- Model IDs and vendor limits in PLAN.md are tagged **[VERIFY]**: confirm against current vendor docs (the planned 15-minute smoke test, PLAN.md §19 Step 0) before relying on them. Never invent numbers; every result in any document must come from a real run.

## Architecture (as planned in PLAN.md)

FastAPI service, async, **single uvicorn worker** (in-memory rate limiter, locks and the SQLite registry depend on one process). `app/main.py` holds routes only; everything else sits in layers:

- **Two stores that must stay consistent:** Qdrant holds vectors plus chunk text/metadata (one collection, filtered by `metadata.owner_id`; point id = uuid5 of `{doc_id}:{chunk_idx}`), and SQLite holds the **document registry** (and users for B1). The registry is the source of truth: upload writes its row *last* and deletes the document's points by filter on failure; delete removes the vectors with one filtered delete, then the row; `/ask` drops any hit whose `doc_id` is not in the registry. Writes use `wait=True`, so new points are searchable when the upsert returns.
- **All OpenAI traffic goes through one wrapper (`app/llm_guard.py`, LangChain models built with `max_retries=0`)**: per-service semaphores, timeouts, bounded retries honouring `Retry-After`, SDK retries disabled, no retry on `insufficient_quota`, one request deadline, upstream failures mapped to 503. Evals use the same wrapper.
- **Per-user limits:** `MAX_DOCS_PER_USER` (10), `MAX_CHUNKS_PER_USER` (5000) and `DAILY_TOKEN_LIMIT_PER_USER` (100000), 0 = off. Storage limits are checked in `IngestionService` (early) and in `Database.add_document` (atomic, under the lock); the token limit in `AskService.ask` before any model call, with usage recorded in the `token_usage` table in a `finally`. Scripts that measure behaviour (evals, notebooks, edge cases) switch the limits off.
- **Ownership model:** every service function takes an `owner_id`. With `AUTH_ENABLED=true` (the default since B1 was built) every document and question route needs a JWT; with `false` routes use a fixed default owner. Evals use internal owners `eval_A` / `eval_B` and separate SQLite files `data/eval_A.db` / `data/eval_B.db` plus the Qdrant collection `<QDRANT_COLLECTION>_eval`.
- **Ask pipeline order:** rate limit → JWT (B1) → validation → input guardrails (English pattern checks; a model rechecks soft blocks and only an exact `OK` releases them, fail-closed; then a model screen runs in parallel with the embedding and can only block, before the Qdrant search) → registry empty check → embed → Qdrant query → registry filter → similarity threshold (also the off-topic mechanism; below it the LLM is **not** called) → prompt with numbered context → LLM → citation parse/validate → output guardrails (invented citations, prompt/key leak). Refusals are HTTP 200 with `refused` + `reason`; upstream failures are 503; one error JSON shape everywhere, including 422.
- **Prompt contract:** context is data, not instructions; exact `NOT_IN_DOCUMENTS` token becomes a fixed English refusal; citations are `[n]` into the retrieved chunks. Citation validation proves a citation exists, not that it supports the claim; faithfulness in the eval measures that.
- **Evaluation (`evals/`):** own hit@k / recall@k / MRR using evidence-span labels (no chunk ids, so two chunking configs A 1000/200 and B 500/100 are comparable), own LLM-as-judge with a different, larger model than the generator, hand-labelled judge reliability, a fixed winner rule, disk cache keyed on model/prompt/config/input. One command reproduces everything (`python -m evals.run_eval --config all`).

## Commands (Windows; venv at `.venv`, Python 3.14)

- Setup: `python -m venv .venv`, `.venv/Scripts/activate`, `pip install -r requirements.txt` (notebook builder additionally needs `nbformat nbclient ipykernel`)
- Run: `uvicorn app.main:app` (single worker; UI at `/`, Swagger at `/docs`; starts without OpenAI or Qdrant keys, `/health` then reports `qdrant: false`; with the default `AUTH_ENABLED=true` it refuses to start unless `JWT_SECRET` has 32+ characters, so set one or use `AUTH_ENABLED=false`)
- Tests (offline, ~10 s): `pytest -q`; single test: `pytest tests/test_api.py::test_upload_list_ask_delete_reask -q`
- Guardrail proof table: `python scripts/run_guardrail_cases.py --mocked` (needs a running server for the HTTP part)
- Evaluation: `python -m evals.run_eval --config all` (needs keys, `corpus/`, `evals/golden_set.json`)
- Notebooks: `python scripts/make_notebooks.py` (executes them; real services if keys exist, otherwise labelled offline mode)

## Working notes

- Keep PLAN.md section cross-references (`§n`) and the §20 risk numbering correct when editing.
- The owner's shell is Windows 10 (PowerShell/Git Bash); paths in this repo contain no spaces, but the user profile path does.
