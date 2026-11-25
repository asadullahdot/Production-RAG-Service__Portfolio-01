import { useEffect, useRef, useState } from "react";
import { api, json } from "../api.js";
import { formatResetTime, isFull, isSendKey, limitText, scopeIds, sumTokens } from "../lib.js";
import Turn from "./Turn.jsx";
import Logo from "./Logo.jsx";
import { DESCRIPTION, TAGLINE } from "../brand.js";

const MAX = 1000;  // the server's MAX_QUESTION_CHARS
const EXAMPLES = ["Summarise this in five bullet points.", "What are the key dates and numbers?", "What risks or limitations are mentioned?"];

export default function Chat({ usage, onAnswered, docs, loading, excluded, token, onOpenTrace }) {
  const [turns, setTurns] = useState([]);
  const [text, setText] = useState("");
  const box = useRef(null);
  const end = useRef(null);

  const ids = scopeIds(docs, excluded);
  const noDocs = !loading && docs.length === 0;
  const noneSelected = docs.length > 0 && ids && ids.length === 0;
  const pending = turns.some((t) => t.status === "pending");
  // True when today's token allowance is used up: the message box is disabled and a note says when it starts again. The server
  // enforces it too (429 token_quota_exceeded); this just avoids sending a question that is certain to be refused.
  const tokensUsedUp = isFull(usage?.tokens_today);
  const canSend = text.trim().length > 0 && !pending && !noDocs && !noneSelected && !loading && !tokensUsedUp;

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
    onAnswered?.();  // the question (even a failed one) may have spent tokens: refresh the used-today figure
    box.current?.focus();
  }

  const tokens = sumTokens(turns.map((t) => t.result));
  const scope = docs.length === 0 ? "" : !ids ? `Answers use all ${docs.length} document${docs.length === 1 ? "" : "s"}.` : `Answers use ${ids.length} of ${docs.length} documents.`;

  return (
    <section aria-labelledby="ask-title" className="flex min-h-[60dvh] min-w-0 flex-col">
      <h2 id="ask-title" className="sr-only">Ask a question</h2>

      <div className="flex-1 space-y-5">
        {turns.length === 0 ? (
          <div className="card px-5 py-8 text-center">
            <Logo className="mx-auto mb-3 size-12" />
            <p className="text-xl font-semibold">{TAGLINE}</p>
            <p className="muted mx-auto mt-1 max-w-md text-base">{DESCRIPTION}</p>
            {noDocs && <p className="muted mt-3">Upload a document to get started.</p>}
            {!loading && !noDocs && (
              <div className="mt-5 flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" className="chip" onClick={() => { setText(ex); box.current?.focus(); }}>{ex}</button>
                ))}
              </div>
            )}
          </div>
        ) : (
          turns.map((t) => <Turn key={t.id} turn={t} onTrace={onOpenTrace} />)
        )}
        <div ref={end} />
      </div>

      <div className="sticky bottom-0 -mx-4 mt-4 bg-linear-to-t from-slate-50 via-slate-50/90 to-transparent px-4 pb-3 pt-4 dark:from-slate-950 dark:via-slate-950/90">
        <form onSubmit={(e) => { e.preventDefault(); send(); }}
          className="flex items-end gap-2 rounded-[1.75rem] border border-slate-200/70 bg-white p-1.5 pl-5 shadow-xl shadow-indigo-500/10 focus-within:border-indigo-400 dark:border-slate-700 dark:bg-slate-900">
          <label htmlFor="question" className="sr-only">Your question</label>
          <textarea
            id="question" ref={box} rows={1} maxLength={MAX} value={text} disabled={noDocs || noneSelected || tokensUsedUp}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (isSendKey(e.nativeEvent)) { e.preventDefault(); send(); } }}
            placeholder={noDocs ? "Upload a document to start asking" : noneSelected ? "Tick at least one document" : tokensUsedUp ? "Daily token limit reached" : "Ask about your documents"}
            className="max-h-[200px] min-h-11 flex-1 resize-none bg-transparent px-0 py-2 text-base outline-none focus-visible:outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
          />
          <button type="submit" className="btn-primary shrink-0" disabled={!canSend} aria-label="Send question">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </form>
        {tokensUsedUp && (
          <p role="status" className="mt-2 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            You have used today's token allowance. It starts again at {formatResetTime(usage.tokens_reset_at)} your time (00:00 UTC).
          </p>
        )}
        <p className="muted mt-1 flex flex-wrap justify-between gap-x-4 px-1">
          <span>Enter to send · Shift+Enter for a new line{scope && ` · ${scope}`}{usage ? ` · ${limitText(usage.tokens_today)} tokens today` : tokens > 0 && ` · ${tokens.toLocaleString()} tokens this session`}</span>
          {text.length > MAX * 0.8 && <span aria-live="polite">{text.length}/{MAX}</span>}
        </p>
      </div>
    </section>
  );
}
