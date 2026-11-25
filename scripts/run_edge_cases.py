"""B7: run every edge case from the brief against the real service and print a PASS/FAIL table (for the demo video).

The script starts its OWN servers (port 8771 plus short-lived ones for the failure cases) with a separate SQLite file
(data/edge_cases.db) and a separate Qdrant collection (<QDRANT_COLLECTION>_edge), logins off, so it never touches your
documents. It uses the real OpenAI and Qdrant, so it needs the keys in .env. At the end it deletes its database and
collection. Result file: evals/results/b7_edge_cases.json.

Run: python scripts/run_edge_cases.py          (about 3-5 minutes)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_edge_case_fixtures import SMALL, long_pdf, too_big_pdf  # noqa: E402

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
MIME = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
RESULTS: list[dict] = []
SERVERS: list[subprocess.Popen] = []


def collection_name() -> str:
    from app.config import get_settings
    return get_settings().qdrant_collection + "_edge"


def start_server(port: int, extra: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({"AUTH_ENABLED": "false", "SQLITE_PATH": str(ROOT / "data" / "edge_cases.db"), "QDRANT_COLLECTION": collection_name(),
                "RATE_LIMIT_PER_MIN": "1000", "PYTHONIOENCODING": "utf-8",
                "MAX_DOCS_PER_USER": "0", "MAX_CHUNKS_PER_USER": "0", "DAILY_TOKEN_LIMIT_PER_USER": "0"})  # the cases are about files, not limits
    env.update(extra or {})
    log = open(ROOT / "data" / f"edge_cases_server_{port}.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    SERVERS.append(proc)
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code in (200, 503):
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"server on port {port} did not start; see data/edge_cases_server_{port}.log")


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def record(brief: str, name: str, expected: str, ok: bool, observed: str, seconds: float) -> None:
    RESULTS.append({"brief_item": brief, "case": name, "expected": expected, "observed": observed, "pass": bool(ok), "seconds": round(seconds, 1)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name:52} {observed[:100]}", flush=True)


def upload(c: httpx.Client, name: str, data: bytes, mime: str | None = None) -> httpx.Response:
    return c.post("/documents", files={"file": (name, data, mime or MIME.get(Path(name).suffix, "application/octet-stream"))})


def ask(c: httpx.Client, q: str, **kw) -> dict:
    return c.post("/ask", json={"question": q, **kw}).json()


def reset(c: httpx.Client) -> None:
    for d in c.get("/documents").json().get("documents", []):
        c.delete(f"/documents/{d['doc_id']}")


def fixture(name: str) -> bytes:
    return SMALL[name]()


def err(r: httpx.Response) -> str:
    return r.json().get("error", {}).get("code", "")


def run_normal_cases() -> None:
    with httpx.Client(base_url=BASE, timeout=180) as c:
        # 1. a question before any document
        t = time.time(); reset(c); j = ask(c, "What is the refund policy?")
        record("question before any upload", "ask with no documents", "refused, reason no_documents", j["refused"] and j["reason"] == "no_documents", f"refused={j['refused']} reason={j['reason']}", time.time() - t)

        # 2-3. empty and scanned PDFs
        for name, label in (("empty.pdf", "empty PDF (no text)"), ("scanned.pdf", "scanned PDF (image only, no text layer)")):
            t = time.time(); r = upload(c, name, fixture(name))
            record("scanned or empty PDF", label, "400 no_text", r.status_code == 400 and err(r) == "no_text", f"{r.status_code} {err(r)}", time.time() - t)

        # 4. the same file twice
        reset(c); t = time.time(); data = fixture("table_prices.docx")
        first = upload(c, "prices.docx", data); second = upload(c, "prices.docx", data)
        dup_ok = first.status_code == 201 and second.status_code == 409 and err(second) == "duplicate_document" and second.json()["error"]["details"]["doc_id"] == first.json()["doc_id"]
        c.delete(f"/documents/{first.json()['doc_id']}"); third = upload(c, "prices.docx", data)
        record("same file twice", "upload the same file twice, then delete and re-upload", "201, 409 with the existing doc_id, then 201", dup_ok and third.status_code == 201, f"{first.status_code}, {second.status_code}, then {third.status_code}", time.time() - t)

        # 5. a very large file
        reset(c); t = time.time(); r = upload(c, "huge.pdf", too_big_pdf())
        record("very large file", "12 MB file (limit 10 MB)", "413 file_too_large", r.status_code == 413 and err(r) == "file_too_large", f"{r.status_code} {err(r)}", time.time() - t)

        # 6. a very long document: over the page limit is refused, a long one within the limit works
        t = time.time(); r = upload(c, "250_pages.pdf", long_pdf(250))
        record("very long document", "250-page PDF (limit 200 pages)", "413 file_too_large", r.status_code == 413 and err(r) == "file_too_large", f"{r.status_code} {err(r)}", time.time() - t)
        t = time.time(); r = upload(c, "120_pages.pdf", long_pdf(120)); ok = r.status_code == 201
        j = ask(c, "Where is the emergency generator located?") if ok else {}
        hit = ok and not j["refused"] and "seven" in j["answer"].lower() and any(ch["page"] == 100 for ch in j["retrieved_chunks"])
        record("very long document", "120-page PDF accepted, fact from page 100 found", "201, answer 'building seven', chunk from page 100", hit,
               f"{r.status_code}; answer={j.get('answer', '')[:60]!r}", time.time() - t)
        reset(c)

        # 7. a question the documents cannot answer
        upload(c, "prices.docx", fixture("table_prices.docx")); t = time.time(); j = ask(c, "Who is the chief executive officer of the company?")
        record("question the documents cannot answer", "unanswerable question", "refused", j["refused"], f"refused={j['refused']} reason={j['reason']}", time.time() - t)

        # 8. a question about a deleted document
        reset(c); r = upload(c, "french.txt", fixture("french_horaires.txt")); doc_id = r.json()["doc_id"]; t = time.time()
        before = ask(c, "When is the municipal library open?"); c.delete(f"/documents/{doc_id}")
        after = ask(c, "When is the municipal library open?"); targeted = c.post("/ask", json={"question": "library hours", "doc_ids": [doc_id]})
        gone = (not before["refused"]) and after["refused"] and after["reason"] == "no_documents" and targeted.status_code == 404
        record("question about a deleted document", "ask, delete the document, ask again", "answered, then refused (no_documents), and 404 when targeting it", gone,
               f"before refused={before['refused']}; after reason={after['reason']}; targeted {targeted.status_code}", time.time() - t)

        # 9. two documents that contradict each other
        reset(c); upload(c, "policy_2023.pdf", fixture("contradict_2023.pdf")); upload(c, "policy_2024.docx", fixture("contradict_2024.docx")); t = time.time()
        j = ask(c, "How many days do customers have to return an item?"); ans = j["answer"]
        files = {x["file_name"] for x in j["citations"]}
        record("two documents that contradict each other", "30 days (2023) versus 14 days (2024)", "the answer gives both figures and cites both documents",
               (not j["refused"]) and "30" in ans and "14" in ans and len(files) == 2, f"cited {sorted(files)}; answer={ans[:90]!r}", time.time() - t)

        # 10. tables, special characters, non-English text
        reset(c); upload(c, "prices.docx", fixture("table_prices.docx")); t = time.time(); j = ask(c, "What is the price of Widget B and is it in stock?")
        record("tables in files", "price table in a Word file", "$99.00 and backordered", (not j["refused"]) and "99" in j["answer"] and "backorder" in j["answer"].lower(), f"answer={j['answer'][:80]!r}", time.time() - t)
        reset(c); upload(c, "cafe.md", fixture("special_chars.md")); t = time.time(); j = ask(c, "What is the Wi-Fi password of the cafe?")
        record("special characters in files", "accents, ligature, emoji, curly quotes, RTL marks", "the password with its special characters", (not j["refused"]) and "Œuvre-42" in j["answer"], f"answer={j['answer'][:80]!r}", time.time() - t)
        reset(c); upload(c, "horaires.txt", fixture("french_horaires.txt")); t = time.time()
        j_en = ask(c, "When is the municipal library open?"); j_fr = ask(c, "Quand la bibliothèque est-elle ouverte ?"); j_no = ask(c, "Who is the mayor of the city?")
        ok = (not j_en["refused"]) and any(w in j_en["answer"].lower() for w in ("nine", "9", "neuf")) and (not j_fr["refused"]) and j_no["refused"] and "documents do not contain" in j_no["answer"]
        record("non-English text in files", "French document, English and French questions, unanswerable one", "answered in both; the refusal text stays English", ok,
               f"EN {j_en['answer'][:45]!r}; FR refused={j_fr['refused']}; unanswerable refused={j_no['refused']}", time.time() - t)
        reset(c); upload(c, "urdu.docx", fixture("urdu_notice.docx")); t = time.time()
        j_en = ask(c, "What are the office hours?"); j_ur = ask(c, "دفتر کے اوقات کیا ہیں؟")
        ok = (not j_en["refused"]) and any(w in j_en["answer"].lower() for w in ("nine", "9", "five", "5", "نو")) and (not j_ur["refused"])
        record("non-English text in files", "Urdu document (right-to-left), English and Urdu questions", "answered", ok, f"EN {j_en['answer'][:60]!r}; UR refused={j_ur['refused']}", time.time() - t)

        # 11. a hostile file name is data, not markup
        reset(c); t = time.time(); evil = "<script>alert(1)</script>.txt"
        r = upload(c, evil, b"The office plants are watered every Tuesday morning by the facilities team."); names = [d["file_name"] for d in c.get("/documents").json()["documents"]]
        record("special characters in files", "file name containing <script> tags", "accepted and returned as plain data (JSON); the UI shows it as text", r.status_code == 201 and names == [evil] and "application/json" in r.headers["content-type"], f"{r.status_code}; stored name={names}", time.time() - t)

        # 12. bad questions
        t = time.time(); r1 = c.post("/ask", json={"question": ""}); r2 = c.post("/ask", json={"question": "x" * 1001})
        record("question the documents cannot answer", "empty question and a 1001-character question", "422 invalid_request for both", r1.status_code == 422 and r2.status_code == 422 and err(r1) == "invalid_request", f"{r1.status_code}, {r2.status_code}", time.time() - t)
        reset(c)


async def run_concurrency() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=240) as c:
        for d in (await c.get("/documents")).json()["documents"]:
            await c.delete(f"/documents/{d['doc_id']}")

        t = time.time(); data = SMALL["table_prices.docx"]()
        rs = await asyncio.gather(*[c.post("/documents", files={"file": ("same.docx", data, MIME[".docx"])}) for _ in range(8)])
        codes = Counter(r.status_code for r in rs); stored = len((await c.get("/documents")).json()["documents"])
        record("several uploads at the same time", "the same file sent 8 times at once", "exactly one stored: 1x 201 and 7x 409", codes == Counter({201: 1, 409: 7}) and stored == 1, f"{dict(codes)}; documents stored={stored}", time.time() - t)
        for d in (await c.get("/documents")).json()["documents"]:
            await c.delete(f"/documents/{d['doc_id']}")

        t = time.time()
        files = [(f"note_{i}.txt", f"Memo number {i}: the meeting room on floor {i} is booked for the {i}th team workshop of the year.".encode()) for i in range(1, 7)]
        rs = await asyncio.gather(*[c.post("/documents", files={"file": (n, b, "text/plain")}) for n, b in files])
        stored = len((await c.get("/documents")).json()["documents"])
        record("several uploads at the same time", "6 different documents at once", "all 6 stored (6x 201)", all(r.status_code == 201 for r in rs) and stored == 6, f"{dict(Counter(r.status_code for r in rs))}; stored={stored}", time.time() - t)

        t = time.time()
        rs = await asyncio.gather(*[c.post("/ask", json={"question": f"What is the meeting room on floor {i} booked for?"}) for i in range(1, 7)] +
                                  [c.post("/ask", json={"question": "Which team workshop is on floor 3?"}) for _ in range(4)])
        ok = all(r.status_code == 200 for r in rs) and sum(1 for r in rs if not r.json()["refused"]) >= 8
        record("several questions at the same time", "10 questions at once", "all 10 answered with HTTP 200 and distinct request ids", ok and len({r.headers["X-Request-ID"] for r in rs}) == 10,
               f"{dict(Counter(r.status_code for r in rs))}; answered={sum(1 for r in rs if not r.json()['refused'])}/10", time.time() - t)
        for d in (await c.get("/documents")).json()["documents"]:
            await c.delete(f"/documents/{d['doc_id']}")


def run_fault_cases() -> None:
    fast = {"MAX_RETRIES": "2", "RETRY_BACKOFF_S": "0.3", "RETRY_BACKOFF_MAX_S": "1"}
    with httpx.Client(base_url=BASE, timeout=180) as normal:
        reset(normal); upload(normal, "horaires.txt", fixture("french_horaires.txt"))  # a document the fault servers can search
        base_docs = len(normal.get("/documents").json()["documents"])
        for fault, label, brief in (("llm_timeout", "the answer model times out", "LLM timing out"), ("llm_429", "the answer model rate-limits us (HTTP 429)", "LLM rate-limiting"),
                                    ("embed_timeout", "the embedding API times out", "embedding API timing out"), ("embed_429", "the embedding API rate-limits us (HTTP 429)", "embedding API rate-limiting"),
                                    ("vector_db_down", "the vector database is down", "dependency outage")):
            proc = start_server(PORT + 1, dict(fast, DEMO_FAULT=fault))
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{PORT + 1}", timeout=180) as c:
                    t = time.time(); r = c.post("/ask", json={"question": "When is the municipal library open?"}); body = r.json()
                    shape = r.status_code == 503 and body.get("error", {}).get("code") == "upstream_unavailable" and bool(body["error"].get("request_id")) and "Traceback" not in r.text and "openai" not in r.text.lower()
                    record(brief, f"ask while {label}", "503 upstream_unavailable after bounded retries, no vendor message or traceback", shape, f"{r.status_code} {body.get('error', {}).get('code')} in {time.time() - t:.1f}s (2 retries)", time.time() - t)
                    if fault in ("embed_timeout", "vector_db_down"):
                        t = time.time(); r = upload(c, "new.txt", b"A brand new document about the quarterly parking rules for visitors and staff alike.")
                        left = len(normal.get("/documents").json()["documents"])
                        record(brief, f"upload while {label}", "503 and nothing half-stored", r.status_code == 503 and left == base_docs, f"{r.status_code}; documents before={base_docs}, after={left}", time.time() - t)
            finally:
                stop(proc)
        t = time.time(); j = ask(normal, "When is the municipal library open?")
        record("dependency outage", "service answers normally again (no restart needed)", "answered", not j["refused"], f"refused={j['refused']}", time.time() - t)
        reset(normal)


def remove_database() -> None:
    """Delete the temporary SQLite files. Windows keeps them locked for a moment after a server stops, so retry."""
    for suffix in ("", "-wal", "-shm"):
        path = ROOT / "data" / f"edge_cases.db{suffix}"
        for _ in range(15):
            try:
                path.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(1)
        else:
            print(f"note: could not delete {path.name} (still locked); it is overwritten on the next run")


def cleanup() -> None:
    for proc in SERVERS:
        if proc.poll() is None:
            stop(proc)
    time.sleep(2)
    remove_database()
    try:
        from qdrant_client import QdrantClient
        from app.config import get_settings
        s = get_settings()
        QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=30).delete_collection(collection_name())
    except Exception as exc:  # cleanup problems must not hide the results
        print("note: could not delete the temporary Qdrant collection:", type(exc).__name__)


def write_results() -> int:
    passed = sum(r["pass"] for r in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} edge cases passed")
    if not RESULTS:  # an aborted run (for example the server did not start) must not overwrite the saved evidence
        print("no case ran; the saved evals/results/b7_edge_cases.json is left as it was")
        return 0
    (ROOT / "evals" / "results" / "b7_edge_cases.json").write_text(json.dumps(
        {"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "passed": passed, "total": len(RESULTS), "cases": RESULTS},
        indent=1, ensure_ascii=False), encoding="utf-8")
    return passed


def main() -> int:
    (ROOT / "data").mkdir(exist_ok=True)
    remove_database()
    print(f"Starting an isolated server on port {PORT} (own database and Qdrant collection '{collection_name()}')...\n")
    try:
        start_server(PORT)
        run_normal_cases()
        asyncio.run(run_concurrency())
        run_fault_cases()
    finally:
        passed = write_results()  # saved first, so a cleanup problem can never lose the results
        cleanup()
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
