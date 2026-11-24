import { useEffect, useState } from "react";
import { reasonText, sourcePreview, splitAnswer, unitLabel } from "../lib.js";

// One question and its answer. Everything from the server is rendered as React text, never as HTML.
export default function Turn({ turn, latest }) {
  const [open, setOpen] = useState(latest);
  useEffect(() => { setOpen(latest); }, [latest]);  // asking a new question folds the older answers' sources
  const [flash, setFlash] = useState(null);
  const r = turn.result;

  function showSource(n) {
    setOpen(true);
    setFlash(n);
    requestAnimationFrame(() => document.getElementById(`chunk-${turn.id}-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
    setTimeout(() => setFlash(null), 1700);
  }

  return (
    <div className="space-y-3">
      <div className="ml-auto w-fit max-w-[90%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2 text-white dark:bg-indigo-500">
        {turn.question}
      </div>

      <div className="card">
        {turn.status === "pending" && (
          <p role="status" className="flex items-center gap-3 text-slate-600 dark:text-slate-300">
            <span className="flex gap-1" aria-hidden="true">
              {[0, 1, 2].map((i) => <span key={i} className="size-2 animate-bounce rounded-full bg-indigo-500" style={{ animationDelay: `${i * 0.15}s` }} />)}
            </span>
            Searching your documents…
          </p>
        )}

        {turn.status === "error" && (
          <p role="alert" className="rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            {turn.error}
          </p>
        )}

        {turn.status === "done" && (
          <>
            {r.refused && (
              <p className="mb-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
                <strong>No answer.</strong> {reasonText(r.reason)}
              </p>
            )}
            <p className="whitespace-pre-wrap break-words leading-relaxed">
              {splitAnswer(r.answer, r.citations).map((part, i) =>
                part.type === "cite" ? (
                  <button key={i} type="button" onClick={() => showSource(part.n)} aria-label={`Show source ${part.n}`}
                    className="mx-0.5 inline-flex min-h-6 min-w-6 cursor-pointer items-center justify-center rounded-md bg-indigo-100 px-1.5 align-baseline text-sm font-semibold text-indigo-800 hover:bg-indigo-200 dark:bg-indigo-950 dark:text-indigo-200 dark:hover:bg-indigo-900">
                    {part.n}
                  </button>
                ) : (
                  <span key={i}>{part.text}</span>
                ),
              )}
            </p>
            <p className="muted mt-3">
              {r.num_retrieved} {r.num_retrieved === 1 ? "chunk" : "chunks"} retrieved · {(r.latency_ms / 1000).toFixed(1)} s · {r.usage.input_tokens + r.usage.output_tokens} tokens
            </p>

            {r.retrieved_chunks.length > 0 && (
              <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)} className="group mt-2">
                <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 font-medium [&::-webkit-details-marker]:hidden">
                  <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4 shrink-0 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                  <span className="shrink-0">Sources ({r.retrieved_chunks.length})</span>
                  {!open && <span className="muted min-w-0 truncate font-normal">{sourcePreview(r.retrieved_chunks, r.citations)}</span>}
                </summary>
                <ol className="mt-1 space-y-3">
                  {r.retrieved_chunks.map((c) => {
                    const cited = r.citations.some((x) => x.n === c.n);
                    return (
                      <li key={c.n} id={`chunk-${turn.id}-${c.n}`}
                        className={`rounded-xl border p-3 ${flash === c.n ? "flash" : ""} ${cited ? "border-indigo-300 dark:border-indigo-800" : "border-slate-200 dark:border-slate-800"}`}>
                        <p className="muted flex flex-wrap items-center gap-x-2 break-words">
                          <span className="inline-flex size-6 items-center justify-center rounded-md bg-slate-200 text-xs font-semibold text-slate-800 dark:bg-slate-800 dark:text-slate-100">{c.n}</span>
                          <span className="min-w-0 break-all">{c.file_name}</span>
                          <span>· {unitLabel(c.file_name)} {c.page}</span>
                          <span>· similarity {c.score.toFixed(3)}</span>
                          {cited && <span className="font-medium text-indigo-700 dark:text-indigo-300">· cited</span>}
                        </p>
                        <p className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-sm">{c.text}</p>
                      </li>
                    );
                  })}
                </ol>
              </details>
            )}
          </>
        )}
      </div>
    </div>
  );
}
