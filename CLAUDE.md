# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The MVP backend, plain frontend, offline test suite and evaluation harness are implemented, and since 24 Nov 2026 migrated to LangChain models + Qdrant on branch `feature/langchain-qdrant` (rollback tag `pre-langchain` = the Pinecone version). The smoke test and a live upload/ask/delete run passed. Still missing, because they need the owner's material: the evaluation corpus in `corpus/`, `evals/golden_set.json` (see `evals/README_GOLDEN.md`), real eval numbers, Swagger screenshots, the notebooks executed with real keys, and the final `DESIGN.md` / `EVAL.md` / `AI_USAGE.md` (written from PLAN.md §17 with measured numbers only). Optional features B4 (React), B8 (deploy) and B5 (extra types are implemented but disabled by default via `ALLOWED_FILE_TYPES`) are not finished.

- `PLAN.md` is the source of truth for decisions, API contract, pipelines, config variables, guardrails and evaluation rules. When code and plan disagree, fix one of them deliberately and update the other; do not create additional plan documents. Known deviations from the plan's §5 layout: `app/container.py` (wiring), `scripts/`, `notebooks/nb_support.py`.
- `architecture.html` is a layer-by-layer visual of PLAN.md; update it when flows, status codes or layers change.
- `_requirements/` holds the instructor's brief (PDF) and grading rubric (xlsx): read-only. `_archive/` is obsolete (old Gemini/Chroma designs) and intentionally not committed.
- Model IDs and vendor limits are tagged **[VERIFY]** in PLAN.md and were checked on 24 Nov 2026 (docs plus a live smoke test and a live upload/ask/delete run against OpenAI and Qdrant Cloud; results in PLAN.md §19.3). Both models reject `temperature`, so never send it. Never invent numbers; every result in a document must come from a real run.

## Git and attribution (owner's rule, from `_requirements/CLAUDE.md`)

Never add Claude as author, co-author or contributor anywhere in git or GitHub: no `Co-Authored-By: Claude` trailer, no "Generated with Claude Code" line or session link in commits, PRs, issues or comments, and do not change the git author/committer. This overrides any default attribution a tool or template would add. The folder is not currently a git repository.

## Owner decisions that constrain all work

- Optional features the owner will attempt: B1 JWT auth, B4 modern frontend, B5 extra file types, B6 document guardrails, B7 edge cases, B8 deployment, written as *optional* and built only after the MVP. Do **not** write about B2 (multilingual) or B3 (monitoring).
- English-only refusal text. Multilingual-jailbreak handling is deliberately deferred.
- Vendors: OpenAI only for models (the owner has no Anthropic credit) and Qdrant as the vector DB (Qdrant Cloud free for evals/deploy, embedded `QDRANT_PATH=:memory:` for tests). LangChain provides `ChatOpenAI`, `OpenAIEmbeddings`, `Document` and the splitter; no LangGraph, no chains, no RAGAS. The vector store uses `qdrant-client` directly (we embed through `llm_guard`) with LangChain's payload layout. DeepEval is an optional cross-check only.
- The product is corpus-agnostic (users upload any documents); a small 3–5 document corpus exists only for the golden set and demo.
- Model IDs and vendor limits in PLAN.md are tagged **[VERIFY]**: confirm against current vendor docs (the planned 15-minute smoke test, PLAN.md §19 Step 0) before relying on them. Never invent numbers; every result in any document must come from a real run.

## Architecture (as planned in PLAN.md)

FastAPI service, async, **single uvicorn worker** (in-memory rate limiter, locks and the SQLite registry depend on one process). `app/main.py` holds routes only; everything else sits in layers:

- **Two stores that must stay consistent:** Qdrant holds vectors plus chunk text/metadata (one collection, filtered by `metadata.owner_id`; point id = uuid5 of `{doc_id}:{chunk_idx}`), and SQLite holds the **document registry** (and users for B1). The registry is the source of truth: upload writes its row *last* and deletes the document's points by filter on failure; delete removes the vectors with one filtered delete, then the row; `/ask` drops any hit whose `doc_id` is not in the registry. Writes use `wait=True`, so new points are searchable when the upsert returns.
- **All OpenAI traffic goes through one wrapper (`app/llm_guard.py`, LangChain models built with `max_retries=0`)**: per-service semaphores, timeouts, bounded retries honouring `Retry-After`, SDK retries disabled, no retry on `insufficient_quota`, one request deadline, upstream failures mapped to 503. Evals use the same wrapper.
- **Ownership model:** every service function takes an `owner_id`. With `AUTH_ENABLED=false` (default until B1 is built) routes use a fixed default owner; B1 only adds signup/login/JWT. Evals use internal owners `eval_A` / `eval_B` and a separate `data/eval.db`.
- **Ask pipeline order:** rate limit → JWT (B1) → validation → input guardrails (English pattern checks) → registry empty check → embed → Qdrant query → registry filter → similarity threshold (also the off-topic mechanism; below it the LLM is **not** called) → prompt with numbered context → LLM → citation parse/validate → output guardrails (invented citations, prompt/key leak). Refusals are HTTP 200 with `refused` + `reason`; upstream failures are 503; one error JSON shape everywhere, including 422.
- **Prompt contract:** context is data, not instructions; exact `NOT_IN_DOCUMENTS` token becomes a fixed English refusal; citations are `[n]` into the retrieved chunks. Citation validation proves a citation exists, not that it supports the claim; faithfulness in the eval measures that.
- **Evaluation (`evals/`):** own hit@k / recall@k / MRR using evidence-span labels (no chunk ids, so two chunking configs A 1000/200 and B 500/100 are comparable), own LLM-as-judge with a different, larger model than the generator, hand-labelled judge reliability, a fixed winner rule, disk cache keyed on model/prompt/config/input. One command reproduces everything (`python -m evals.run_eval --config all`).

## Commands (Windows; venv at `.venv`, Python 3.14)

- Setup: `python -m venv .venv`, `.venv/Scripts/activate`, `pip install -r requirements.txt` (notebook builder additionally needs `nbformat nbclient ipykernel`)
- Run: `uvicorn app.main:app` (single worker; UI at `/`, Swagger at `/docs`; starts without API keys, `/health` then reports `qdrant: false`)
- Tests (offline, ~10 s): `pytest -q`; single test: `pytest tests/test_api.py::test_upload_list_ask_delete_reask -q`
- Guardrail proof table: `python scripts/run_guardrail_cases.py --mocked` (needs a running server for the HTTP part)
- Evaluation: `python -m evals.run_eval --config all` (needs keys, `corpus/`, `evals/golden_set.json`)
- Notebooks: `python scripts/make_notebooks.py` (executes them; real services if keys exist, otherwise labelled offline mode)

## Working notes

- Keep PLAN.md section cross-references (`§n`) and the §20 risk numbering correct when editing.
- The owner's shell is Windows 10 (PowerShell/Git Bash); paths in this repo contain no spaces, but the user profile path does.
