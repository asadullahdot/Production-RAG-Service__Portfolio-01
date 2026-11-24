import { useEffect, useState } from "react";
import { api } from "../api.js";
import { formatCost, formatMs, formatCount, spanDetail, statusTone, stepLabel, waterfallGeometry } from "../lib.js";

export const TONE = {
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  warn: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  bad: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
};

export function StatusBadge({ status }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${TONE[statusTone(status)]}`}>{status}</span>;
}

// The steps of one request as a waterfall: each bar starts when the step started and lasts as long as the step took.
export default function TraceView({ token, requestId, onClose }) {
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setTrace(null);
    setError("");
    api(`/metrics/requests/${encodeURIComponent(requestId)}`, { token })
      .then((t) => alive && setTrace(t))
      .catch((e) => alive && setError(e.status === 404 ? "This trace is no longer stored." : e.message));
    return () => { alive = false; };
  }, [requestId, token]);

  const total = trace ? trace.spans[0]?.duration_ms || trace.request.latency_ms : 0;
  const geo = trace ? waterfallGeometry(trace.spans, total) : [];

  return (
    <section className="card" aria-labelledby="trace-title">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="trace-title" className="text-base font-semibold">Trace</h3>
          <p className="muted wrap-anywhere">Request {requestId}</p>
        </div>
        <button type="button" className="btn-ghost shrink-0" onClick={onClose}>Close</button>
      </div>

      {error && <p role="alert" className="mt-3 text-red-700 dark:text-red-300">{error}</p>}
      {!trace && !error && <p className="muted mt-3">Loading…</p>}

      {trace && (
        <>
          <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="font-medium capitalize">{trace.request.route}</span>
            <StatusBadge status={trace.request.status} />
            <span>{formatMs(total)} total</span>
            <span>{formatCount(trace.request.input_tokens + trace.request.output_tokens)} tokens</span>
            <span>{formatCost(trace.request.cost_usd)} (estimate)</span>
            {trace.request.outcome === "refused" && <span className="text-amber-800 dark:text-amber-200">refused: {trace.request.reason}</span>}
            {trace.request.error_code && <span className="text-red-700 dark:text-red-300">{trace.request.error_code}</span>}
          </p>
          <ol className="mt-4 space-y-3">
            {trace.spans.map((s, i) => {
              const detail = spanDetail(s.attrs);
              const color = s.status === "error" ? "bg-red-500" : s.depth === 0 ? "bg-slate-400 dark:bg-slate-500" : s.name.startsWith("llm") ? "bg-violet-500" : "bg-indigo-500";
              return (
                <li key={s.seq} title={s.name}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 wrap-anywhere" style={{ paddingLeft: `${Math.min(s.depth, 3) * 14}px` }}>
                      <span className={s.depth === 0 ? "font-semibold" : ""}>{stepLabel(s.name)}</span>
                      {detail && <span className="muted ml-2">{detail}</span>}
                    </span>
                    <span className="shrink-0 tabular-nums text-slate-600 dark:text-slate-300">{formatMs(s.duration_ms)}</span>
                  </div>
                  <div className="relative mt-1 h-2.5 rounded-full bg-slate-100 dark:bg-slate-800" role="img"
                    aria-label={`${stepLabel(s.name)} started at ${formatMs(s.offset_ms)} and took ${formatMs(s.duration_ms)}`}>
                    <div className={`absolute top-0 h-full rounded-full ${color}`} style={{ left: `${geo[i].left}%`, width: `${geo[i].width}%` }} />
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="muted mt-4">Bars show when each step started and how long it took, relative to the whole request. Steps that overlap ran in parallel.</p>
        </>
      )}
    </section>
  );
}
