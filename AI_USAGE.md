# AI usage

This file says which AI tools were used on this project, how, and who did what. It is kept up to date as the work proceeds (last updated 25 Nov 2026).

## 1. Tools and models

| Tool | Used for | Notes |
|---|---|---|
| **Claude Code** (VS Code extension), Claude models (this part of the work ran on `claude-sonnet-5-5`) | Planning, writing and reviewing all code, tests, scripts, notebooks and documents in this repository; running the real smoke tests and evaluation; code reviews through its `/code-review` command | Ran with file, shell and web-search/web-fetch tools in the owner's checkout. A `web-fetch` sub-agent read vendor documentation pages (OpenAI, Qdrant, Hugging Face) |
| **OpenAI API**, inside the product | `gpt-6-luna` (answers, guardrail screen and recheck), `gpt-6.1-terra` (evaluation judge), `text-embedding-3-small` (embeddings) | These are the application's own models, not coding assistants. Settings and prices: PLAN.md §3 |
| LangChain | `ChatOpenAI`, `OpenAIEmbeddings`, `Document`, `RecursiveCharacterTextSplitter` | A library used by the application, not an AI assistant |

No other AI assistant was used for the code. The task brief forbids no-code RAG builders; none were used.

## 2. Who did what

**The owner (author)**
- Chose the project's direction and every binding decision: OpenAI as the only model vendor, LangChain without LangGraph, Qdrant as the vector database, English-only refusal text, which optional features to attempt and which to skip, Hugging Face Spaces as the deployment target, and the rule that no AI attribution goes into git.
- Supplied the API keys, the Qdrant Cloud cluster and the evaluation corpus (`corpus/`).
- Tested the application manually and reported real defects that the automated tests had not caught: an ordinary CV question refused as `no_relevant_context` (the similarity threshold was too strict), two attack prompts that slipped past the input guardrail and went on to retrieval, and a Qdrant client/server version warning. Each led to a code change listed in section 4.
- Reviewed and merged the work (merge and push were done on the owner's instruction).

**Claude Code (AI)**
- Wrote the plan (PLAN.md), the application code, the offline test suite (285 tests at the last count), the evaluation harness, the notebooks, the scripts, the golden set and these documents.
- Ran the checks that need real services (smoke test, live upload/ask/delete runs, guardrail measurements) and reported the numbers.
- Reviewed its own code through repeated `/code-review` runs (seven rounds on 24 Nov 2026) and fixed what they found.

**The golden set and the judge check were written or labelled by the AI, not by the owner.** EVAL.md says so next to each number. The owner has not yet independently re-labelled them; that is a limitation of the evaluation, not a hidden fact.

## 3. Chat history

The Claude Code session transcripts are kept locally by Claude Code under `C:\Users\<user>\.claude\projects\e--IEC-Agentic-AI-C-35-Portfolio-01\` (`.jsonl` files). They are **not committed to this public repository**: they contain a third party's CV (name, e-mail and phone number) that the owner pasted into a chat while testing, and tool output that could contain account identifiers. They can be provided to the instructor on request after redaction.

## 4. Significant prompts and what became of them

The owner's wording is paraphrased where the original was long. "Kept" means the output went into the repository essentially as produced; "changed" means it was corrected or redirected.

| Date | Prompt (summary) | Tool | Result |
|---|---|---|---|
| 23 Nov | Do not write code until I say; write a master plan from the brief and rubric | Claude Code | PLAN.md created (architecture, API contract, pipelines, guardrails, evaluation rules). Kept, then rewritten twice |
| 23 Nov | Start building the MVP (repo URL given) | Claude Code | First backend: own-code pipeline with Pinecone, frontend, 121 offline tests. **Replaced** on 24 Nov (see below) |
| 23 Nov | Why was LangChain not chosen? Give a solid reason; check the brief for framework requirements; I recommend LangChain as the framework | Claude Code | Brief re-read: a framework is optional. Owner chose LangChain ("Option C"), reversing the earlier own-code decision |
| 23 Nov | Plan option C in detail; suggest a vector database; go with Qdrant; do not over-engineer, use LangChain but not LangGraph | Claude Code | PLAN.md updated: LangChain models and splitter, Qdrant, plain async pipeline. LangGraph and a custom retriever class removed on the owner's instruction |
| 24 Nov | Verify every `[VERIFY]` in the plan | Claude Code | Package installs, Qdrant behaviour, `ChatOpenAI` payload, model prices and limits checked in a throw-away environment and vendor docs; corrections (client version pin, no async in `QdrantVectorStore`, UUID point ids) recorded in PLAN.md §19.3 |
| 24 Nov | What are the evaluation options? Use Hugging Face for deployment; Qdrant Docker or local, you decide | Claude Code | Decision: Qdrant Cloud free plus embedded mode for tests; Hugging Face limits (paid plan for Docker Spaces, ephemeral disk, port rules) documented in PLAN.md §16 |
| 24 Nov | Rewrite the whole plan, detailed but simple | Claude Code | PLAN.md cut from about 13,200 to 7,800 words, same section numbers. Kept |
| 24 Nov | Keys are in .env, run the smoke test | Claude Code | Live checks against OpenAI and Qdrant Cloud. Finding: `gpt-6-luna` and `gpt-6.1-terra` reject `temperature` (HTTP 400); embedding size 1536 confirmed. Recorded in PLAN.md |
| 24 Nov | Yes, start the migration step by step | Claude Code | Pinecone replaced by Qdrant, own OpenAI wrapper replaced by `llm_guard` over LangChain models, extractor returns `Document`s. Kept. **Changed** from the plan: `qdrant-client` is used directly instead of `langchain-qdrant`, because we embed through `llm_guard` |
| 24 Nov | `/code-review`, then "fix all" (repeated; seven rounds) | Claude Code | Each round's findings were fixed with a regression test (over-broad guardrail patterns, startup race, upload size limit before parsing, judge context, citation parser, proxy header spoofing, retry classification, deadlines, and more). Merged only after the owner asked |
| 24 Nov | Why regex for injection and jailbreak? Can the system prompt handle it? Add a double check for what the regex blocks | Claude Code | Regex kept as the first layer; added a fail-closed model recheck that can release soft pattern matches. Then limited to soft patterns after a review found it could be talked into releasing real attacks |
| 24 Nov | Run `/code-review`, test with the PDF in docs/ three times, merge if no issue | Claude Code | Three live upload/ask/delete rounds passed repeatedly. Merge was **not** done on that instruction because each review round still found new issues; it was done later when the owner asked directly |
| 24 Nov | Owner report: a CV question gets `no_relevant_context` | Claude Code | Reproduced with real embeddings; similarity threshold lowered from 0.30 to 0.15 with measurements recorded in PLAN.md §12 |
| 24 Nov | Owner report: Qdrant client 1.15.1 is incompatible with server 1.20.2 | Claude Code | Client upgraded to 1.20.0; unused `langchain-qdrant` removed |
| 24 Nov | Owner report: two sophisticated attack prompts reached retrieval; detect them before retrieval | Claude Code | New patterns for those shapes, plus a pre-retrieval model screen that runs in parallel with the embedding and can only block. Measured: 0 of 8 evasion attempts missed, 0 of 24 ordinary questions blocked. The screen's first version took 10-14 s per call and was **changed** to run without reasoning (about 2 s) |
| 24 Nov | Merge it to main; push it to GitHub | Claude Code | Done after a secret scan of the tracked files |
| 24 Nov | I added the corpus; create your own golden set; maintain DESIGN.md, EVAL.md and AI_USAGE.md | Claude Code | Golden set written and validated by `scripts/check_golden.py` (38 questions at first, 42 after the speaker-note questions were added); evaluation run; these three documents. Corrections made along the way: two golden spans that Config B cut in half were relabelled, a PPTX extraction gap (speaker notes ignored) was fixed, and the dev-tuned threshold was reported as unreliable with a fixed-0.15 diagnostic added |

| 24 Nov | Redact my resume | Claude Code | Name, contact details and file metadata removed from the DOCX before it went into `corpus/`. Kept |
| 24 Nov | List the bonus features; one at a time: you build, I test, I confirm, you build the next | Claude Code | Workflow used for B5, B1, B6 and B7: one branch per feature, merged to `main` only after the owner wrote "confirmed". Order and scope in PLAN.md section 16 |
| 24 Nov | B5 (more file types) | Claude Code | PPTX (with speaker notes and grouped shapes), TXT and MD, on by default; tests per type. Kept |
| 24 Nov | B1 (login) | Claude Code | Argon2id, pinned-HS256 JWT, per-user isolation checked live with two users. A mandatory 32-character `JWT_SECRET` was generated into the git-ignored `.env`. Kept |
| 24 Nov | B6 (document guardrails) | Claude Code | PDF active-content rejection, a scanner that flags text aimed at an AI, labelled context passages. Measured 0 of 33 answers obeyed a planted instruction (before: 2 of 33). A held-out check showed the scanner generalises poorly (2 of 6 unseen attacks), and the result is reported that way. **Changed** during testing: two over-generic scanner patterns were removed after they flagged benign sentences |
| 24-25 Nov | B7 (edge cases) | Claude Code | 27 cases against the real services, all passing. The run found a real bug (prompt rule 6 refused to quote a password written in a user's document); the rule was reworded and the evaluation re-run (EVAL.md section 9). The runner itself crashed on Windows on its first run (a locked SQLite file) and was fixed. Merged after the owner's confirmation |
| 25 Nov | Do not do the deployment yet | Claude Code | B8 (Hugging Face Spaces) was started and stopped on the owner's instruction; the two files created were deleted. It is not built |
| 25 Nov | Do the evaluation wrap-up, then B4; install Node with winget, commit the build, keep it simple, consider the user experience | Claude Code | Evaluation re-run with the corrected prompt (EVAL.md and DESIGN.md updated); B4 React frontend started (see PLAN.md section 16 and DESIGN.md) |

| 25 Nov | `/code-review` on the merged work (7 findings); "double check the nits, then fix all" | Claude Code | Each finding was checked against the code first. **Six were real and fixed:** the PDF check rejected harmless PDFs at random (the 3-byte `/JS` occurs by chance in compressed data; measured about 0.5 false matches per 10 MB of random bytes; the name was dropped, `/JavaScript` and `/Launch` stay), the question limit of 100 document ids broke the UI for people with many documents (now 1000), sign-up followed by a failed login told the user the email was taken, the guardrail runner crashed with a bare KeyError, an unreachable server at page load was treated as "no login needed", and stale README/CLAUDE.md notes. **One was not a bug:** the server already rewrites `[1, 2]` and `[1-3]` into separate `[n]` markers before the UI sees the answer |

## 5. Honest-use statement

Almost all code, tests and prose in this repository were produced by Claude Code under the owner's direction. The owner made the design decisions, supplied the services and material, tested the running application and decided what to merge and publish. Where an AI-written part turned out to be wrong, the table above says so. All numbers in DESIGN.md and EVAL.md come from real runs recorded in `evals/results/` or from the measurements cited in PLAN.md; none were invented.
