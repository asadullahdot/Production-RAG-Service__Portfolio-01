# KNOWLEX: AI-Powered Knowledge Assistant

*“Your Knowledge. One Intelligent Conversation.”* Upload documents. Ask questions. Get evidence-backed answers with citations.

KNOWLEX is the product name of this repository, the Portfolio 01 *Production RAG Service*.

Upload documents (PDF, DOCX, PPTX, TXT, Markdown) and ask questions that are answered **only from those documents**, with numbered citations, a chunks panel (count + similarity scores) and explicit refusals when the answer is not in the documents.

FastAPI · OpenAI (`gpt-6-luna` answers, `text-embedding-3-small` embeddings, `gpt-6.1-terra` as evaluation judge) · Qdrant (one collection, owner-filtered) · SQLite (document registry, users) · LangChain for the model wrappers (`ChatOpenAI`, `OpenAIEmbeddings`), `Document` and the text splitter. The pipeline itself is plain async Python (no chains, no LangGraph).

> **Status (25 Nov 2026):** all mandatory parts and the optional features B1 (login), B3 (monitoring), B4 (React frontend), B5 (file types), B6 (document guardrails) and B7 (edge cases) are implemented and covered by 326 offline tests and 26 frontend tests. B2 (multilingual) was not attempted and B8 (deployment) is not built. Measured results are in [EVAL.md](EVAL.md) (on the test split: hit@5 0.944 for the winning chunking, 1000/200, against 0.833 for 500/100; faithfulness 1.000; 7 of 7 unanswerable questions refused), the design and its trade-offs in [DESIGN.md](DESIGN.md), the AI usage in [AI_USAGE.md](AI_USAGE.md), and the full plan in [PLAN.md](PLAN.md); the layer-by-layer picture is [architecture.html](architecture.html). Swagger screenshots and the demo video link are added by the owner at submission.

Links: repo <https://github.com/asadullahdot/Production-RAG-Service__Portfolio-01> · demo video: _to be added_ · deployed URL: _optional (B8), to be added_

## Features

- **Edge cases (B7):** empty or scanned PDFs, duplicates, very large or very long files, questions before upload or about deleted documents, contradicting documents, tables, special characters, non-English text, simultaneous uploads and questions, and API timeouts or rate limits are all handled. `python scripts/run_edge_cases.py` runs 27 of them against the real services and prints a PASS/FAIL table.
- **Document guardrails (B6):** instructions hidden inside an uploaded file (even white-on-white or in speaker notes) are treated as data and never followed; the document is flagged with a warning in the list. Corrupt, encrypted, macro-carrying, zip-bomb and JavaScript-carrying files are rejected.
- Upload PDF, DOCX, PPTX, TXT or Markdown, list, delete; duplicate detection per user (409). What a "page" is depends on the type: a PDF page, a PowerPoint **slide** (speaker notes and grouped shapes are read too), a **heading section** in Word and Markdown, the whole file for TXT. TXT and Markdown must be UTF-8. Remove types with `ALLOWED_FILE_TYPES` (default `pdf,docx,pptx,txt,md`).
- Ask questions: answer with `[n]` citations linked to the retrieved chunks, `num_retrieved`, per-chunk similarity score; refusals are HTTP 200 with `refused=true` and a `reason`.
- Guardrails: upload (type/MIME/magic bytes, size, page and chunk caps, encrypted/corrupt, zip-bomb, macros), input (empty/too long → 422; English prompt-injection/jailbreak patterns → refused), output (invented citations stripped, system-prompt and API-key leak blocked), document instruction scan.
- One error shape for every failure, `X-Request-ID` on every response, JSON logs with secret redaction, in-memory rate limit (429), bounded retries/timeouts/concurrency for every OpenAI call (upstream failure → 503).
- **Login (B1, on by default):** sign up and log in on the first screen; passwords are stored as Argon2id hashes; login returns a signed JWT (HS256, 60 minutes); every document and question endpoint needs it; each user can only see, search and delete their own documents. `JWT_SECRET` (32+ random characters) is required: generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. Set `AUTH_ENABLED=false` for a no-login demo where everyone shares one owner.
- **Per-user limits:** each user may keep 10 documents and 5,000 chunks and spend 100,000 model tokens per UTC day (`MAX_DOCS_PER_USER`, `MAX_CHUNKS_PER_USER`, `DAILY_TOKEN_LIMIT_PER_USER`; 0 switches a limit off). Over a storage limit an upload gets 403 `quota_exceeded`; over the token limit a question gets 429 `token_quota_exceeded` with the time it works again. `GET /usage` and the UI show how much is used. This protects the owner's OpenAI credit on a public deployment.
- **Monitoring and tracing (B3):** every question, upload, list and delete is traced step by step with OpenTelemetry (embedding, vector search, model calls, guardrails, ...) and stored in SQLite without any question or document text. The **Monitoring** tab shows latency p50/p95, error rate, refusals, tokens, estimated cost per question, where the time goes, requests over time and a waterfall for each request; every answer has a **Trace** link. You only see your own requests. `GET /metrics/summary`, `/metrics/requests`, `/metrics/requests/{request_id}` are in Swagger. `TELEMETRY_ENABLED=false` switches it off.
- **React frontend (B4):** React + Vite + Tailwind, built into `app/static/` and served by FastAPI at `/` (the build is committed, so the app runs without Node). Soft, rounded KNOWLEX design with a branded sign-in screen; chat-style asking (Enter sends, Shift+Enter makes a new line), drag-and-drop upload, tick which documents a question may use, clickable `[n]` citations that open the source chunks (Sources start closed, with a one-line preview), plain-language refusals, delete with confirmation, light and dark theme, works from 360 px. All server text is rendered as React text, never as HTML. The earlier plain HTML/JS version is kept at the git tag `plain-frontend`.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then fill in OPENAI_API_KEY, QDRANT_URL and QDRANT_API_KEY
```

`.env` is git-ignored. The Qdrant collection (`QDRANT_COLLECTION`, cosine, `EMBEDDING_DIM`=1536) is created automatically on startup if it does not exist. For an offline run without a server set `QDRANT_PATH=:memory:`.

### Run

```powershell
uvicorn app.main:app            # http://127.0.0.1:8000  (UI)   /docs  (Swagger)
```

Use a **single worker** (the rate limiter, locks and SQLite registry assume one process).

### Test (fully offline: in-memory vector store, fake embedder, mocked LLM)

```powershell
pytest -q                                   # whole suite
pytest tests/test_api.py::test_upload_list_ask_delete_reask   # a single test
python scripts/run_guardrail_cases.py --mocked   # guardrail proof table (server must be running; --mocked adds output cases)
```

### Evaluate (needs API keys and the corpus + golden set, see [evals/README_GOLDEN.md](evals/README_GOLDEN.md))

```powershell
python -m evals.run_eval --config all
```

Re-ingests the corpus per chunking config (A 1000/200, B 500/100) into an isolated Qdrant collection (`<name>_eval`) and a separate SQLite file, measures hit@k / recall@k / MRR, faithfulness, correctness, unanswerable handling, latency and tokens, tunes the similarity threshold on the dev split, reports the test split and picks the winner by the fixed rule. Outputs are cached in `evals/results/cache/`; results land in `evals/results/`.

### Monitoring demo

With the server running: `python scripts/demo_traffic.py` sends a realistic mix of requests (it creates a demo account and removes its documents afterwards), then open the **Monitoring** tab or run `python scripts/metrics_report.py` for a text report (also saved to `evals/results/b3_metrics.json`). To see failures too, restart once with `DEMO_FAULT=llm_timeout` and run `python scripts/demo_traffic.py --only-questions 3`. Cost is an estimate from published prices.

### Frontend (only needed to change the UI)

Node 20+ is required. `cd frontend`, `npm install`, then `npm run dev` (UI on <http://localhost:5173/static/>, forwards API calls to a running `uvicorn app.main:app` on port 8000), `npm test` (unit tests), and `npm run build` (writes `app/static/`; commit the result).

### Notebooks

`notebooks/01_extract … 05_answer` show each step. They use the real services when keys are present and otherwise run on offline test doubles with a clear banner. Regenerate with `python scripts/make_notebooks.py`.

## API summary

| Method + path | Purpose | Main codes |
|---|---|---|
| `POST /documents` (multipart `file`) | upload + ingest | 201 · 400 · 409 · 413 · 415 · 422 · 429 · 503 |
| `GET /documents` | list own documents | 200 |
| `DELETE /documents/{doc_id}` | delete document and its vectors | 204 · 404 · 503 |
| `POST /ask` | answer a question (refusals are 200) | 200 · 404 · 422 · 429 · 503 |
| `GET /health` | liveness + dependencies, `auth_enabled` | 200 · 503 |
| `GET /usage` | your documents, chunks and tokens used today against your limits | 200 · 401 |
| `GET /metrics/summary`, `GET /metrics/requests`, `GET /metrics/requests/{request_id}` | B3: your latency p50/p95, error rate, tokens, estimated cost, and the trace of a request | 200 · 401 · 404 · 422 |
| `POST /auth/signup`, `POST /auth/login`, `GET /auth/me` | B1 (login form for Swagger's Authorize button; `me` checks the token) | 201/200 · 401 · 403 · 409 · 422 · 429 |

Errors: `{"error": {"code", "message", "request_id", "details"?}}`. Full contract: [PLAN.md §7](PLAN.md).

## Configuration

All settings are environment variables read in `app/config.py` (names in `.env.example`; full table in [PLAN.md §6](PLAN.md)). Highlights: `SIMILARITY_THRESHOLD`, `TOP_K`, `CHUNK_SIZE`/`CHUNK_OVERLAP`, `MAX_UPLOAD_MB`, `RATE_LIMIT_PER_MIN`, `LLM_MAX_CONCURRENCY`, `MAX_RETRIES`, `AUTH_ENABLED`, `MAX_DOCS_PER_USER`, `MAX_CHUNKS_PER_USER`, `DAILY_TOKEN_LIMIT_PER_USER`, `TELEMETRY_ENABLED`, `DEMO_FAULT` (demo only, refused when `ENV=production`: `llm_timeout`, `llm_429`, `embed_timeout`, `embed_429`, `vector_db_down`).

## Project layout

```
app/            main.py (routes only) · config · schemas · errors · db · auth · guardrails · retrieval · generation
                llm_guard (the only model caller) · telemetry · metrics · ratelimit · container · ingestion/ · static/ (built UI)
evals/          golden set, metrics, judge, cache, run_eval, guardrail cases
frontend/       React source (B4); `npm run build` writes app/static/
tests/          pytest suite (offline)         notebooks/  step-by-step notebooks
scripts/        edge-case, guardrail, poisoned-document, monitoring and notebook scripts          corpus/  evaluation corpus and test fixtures
```

## Git workflow

`main` is the stable branch. The MVP (ingestion, ask, guardrails, plain frontend, tests) was built directly on `main` in the first commits. The evaluation and every later feature were built on their own branch and merged with a merge commit: `feature/langchain-qdrant`, `feature/evals`, `feature/auth` (B1), `feature/file-types` (B5), `feature/doc-guardrails` (B6), `feature/edge-cases` (B7), `feature/react-frontend` (B4) and `feature/observability` (B3), plus small `fix/*` and `chore/*` branches for review fixes. The tags `pre-langchain` and `plain-frontend` mark rollback points. `.env` is never committed.

## Known limitations

Prompt-injection/jailbreak checks are English pattern matching only; off-topic detection is a similarity threshold; the citation check proves a citation exists, not that it supports the claim (faithfulness in the evaluation measures that); document text and questions are sent to OpenAI and stored chunks live in Qdrant. See [PLAN.md](PLAN.md) §10 and §20.
