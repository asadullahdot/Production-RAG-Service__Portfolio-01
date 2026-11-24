import { useEffect, useRef, useState } from "react";
import { api, json } from "../api.js";
import { isSendKey, scopeIds } from "../lib.js";
import Turn from "./Turn.jsx";

const MAX = 1000;  // the server's MAX_QUESTION_CHARS
const EXAMPLES = ["Summarise this in five bullet points.", "What are the key dates and numbers?", "What risks or limitations are mentioned?"];

export default function Chat({ docs, loading, excluded, token }) {
  const [turns, setTurns] = useState([]);
  const [text, setText] = useState("");
  const box = useRef(null);
  const end = useRef(null);

  const ids = scopeIds(docs, excluded);
  const noDocs = !loading && docs.length === 0;
  const noneSelected = docs.length > 0 && ids && ids.length === 0;
  const pending = turns.some((t) => t.status === "pending");
  const canSend = text.trim().length > 0 && !pending && !noDocs && !noneSelected && !loading;

  useEffect(() => { if (turns.length) end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  function resize() {  // the box grows with the text up to about 8 lines, then scrolls
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }
  useEffect(resize, [text]);

  async function send() {
    if (!canSend) return;
    const id = Date.now();
    const question = text.trim();
    setTurns((t) => [...t, { id, question, status: "pending" }]);
    setText("");
    try {
      const result = await api("/ask", { token, ...json({ question, doc_ids: ids }) });
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, status: "done", result } : x)));
    } catch (e) {
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, status: "error", error: e.message, question } : x)));
    }
    box.current?.focus();
  }

  const scope = docs.length === 0 ? "" : !ids ? `Answers use all ${docs.length} document${docs.length === 1 ? "" : "s"}.` : `Answers use ${ids.length} of ${docs.length} documents.`;

  return (
    <section aria-labelledby="ask-title" className="flex min-h-[60dvh] flex-col">
      <h2 id="ask-title" className="sr-only">Ask a question</h2>

      <div className="flex-1 space-y-5">
        {turns.length === 0 ? (
          <div className="card text-center">
            <p className="text-lg font-semibold">Ask anything about your documents</p>
            <p className="muted mt-1">
              {loading ? "Loading…" : noDocs ? "Upload a document first, then ask your question here." : "Answers come with numbered sources you can open and check."}
            </p>
            {!loading && !noDocs && (
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" className="btn-ghost" onClick={() => { setText(ex); box.current?.focus(); }}>{ex}</button>
                ))}
              </div>
            )}
          </div>
        ) : (
          turns.map((t) => <Turn key={t.id} turn={t} />)
        )}
        <div ref={end} />
      </div>

      <div className="sticky bottom-0 -mx-4 mt-4 bg-slate-50/90 px-4 pb-3 pt-2 backdrop-blur dark:bg-slate-950/90">
        <form onSubmit={(e) => { e.preventDefault(); send(); }}
          className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-indigo-500 dark:border-slate-700 dark:bg-slate-900">
          <label htmlFor="question" className="sr-only">Your question</label>
          <textarea
            id="question" ref={box} rows={1} maxLength={MAX} value={text} disabled={noDocs || noneSelected}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (isSendKey(e.nativeEvent)) { e.preventDefault(); send(); } }}
            placeholder={noDocs ? "Upload a document to start asking" : noneSelected ? "Tick at least one document" : "Ask about your documents"}
            className="max-h-[200px] min-h-11 flex-1 resize-none bg-transparent px-2 py-2 text-base outline-none focus-visible:outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
          />
          <button type="submit" className="btn-primary shrink-0" disabled={!canSend} aria-label="Send question">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </form>
        <p className="muted mt-1 flex flex-wrap justify-between gap-x-4 px-1">
          <span>Enter to send · Shift+Enter for a new line{scope && ` · ${scope}`}</span>
          {text.length > MAX * 0.8 && <span aria-live="polite">{text.length}/{MAX}</span>}
        </p>
      </div>
    </section>
  );
}
