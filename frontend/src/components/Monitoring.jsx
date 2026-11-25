import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { formatCost, formatCount, formatMs, formatPercent } from "../lib.js";
import { SeriesChart, StepBars } from "./Charts.jsx";
import TraceView, { StatusBadge } from "./TraceView.jsx";

const WINDOWS = [["1h", "1 hour"], ["24h", "24 hours"], ["7d", "7 days"], ["all", "All"]];

function Stat({ label, value, note }) {
  return (
    <div className="rounded-2xl bg-indigo-50/70 p-4 dark:bg-slate-800/50">
      <p className="muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {note && <p className="muted mt-1">{note}</p>}
    </div>
  );
}

export default function Monitoring({ token, initialTrace, onGoChat }) {
  const [win, setWin] = useState("24h");
  const [summary, setSummary] = useState(null);
  const [requests, setRequests] = useState([]);
  const [error, setError] = useState("");
  const [auto, setAuto] = useState(true);
  const [updated, setUpdated] = useState(null);
  const [traceId, setTraceId] = useState(initialTrace?.id || null);

  useEffect(() => { if (initialTrace) setTraceId(initialTrace.id); }, [initialTrace]);

  // The trace sits below the cards and charts: bring it into view and move focus to it when one is opened.
  const traceRef = useRef(null);
  useEffect(() => {
    if (!traceId || !traceRef.current) return;
    traceRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    traceRef.current.focus({ preventScroll: true });
  }, [traceId, initialTrace]);

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api(`/metrics/summary?window=${win}`, { token }), api("/metrics/requests?limit=30", { token })]);
      setSummary(s);
      setRequests(r.requests);
      setUpdated(new Date());
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [win, token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return undefined;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [auto, load]);

  const q = summary?.questions;
  const empty = summary && summary.requests.total === 0;

  return (
    <div className="space-y-4">
      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Monitoring</h2>
            <p className="muted">Your own requests, traced step by step. {updated && <>Updated {updated.toLocaleTimeString()}.</>}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div role="group" aria-label="Time window" className="flex gap-1 rounded-full bg-indigo-50 p-1 dark:bg-slate-950">
              {WINDOWS.map(([value, label]) => (
                <button key={value} type="button" aria-pressed={win === value} onClick={() => setWin(value)}
                  className={`min-h-9 rounded-full px-3 text-sm font-medium transition ${win === value ? "bg-white shadow-md shadow-indigo-500/20 dark:bg-slate-800" : "text-slate-500 dark:text-slate-400"}`}>
                  {label}
                </button>
              ))}
            </div>
            <button type="button" className="btn-ghost" onClick={load}>Refresh</button>
            <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-indigo-600" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              Auto-refresh
            </label>
          </div>
        </div>
        {error && <p role="alert" className="mt-3 text-red-700 dark:text-red-300">{error}</p>}
      </section>

      {/* the trace sits right under the header, so nothing that loads later can push it out of view */}
      {traceId && <TraceView sectionRef={traceRef} token={token} requestId={traceId} onClose={() => setTraceId(null)} />}

      {!summary && !error && <p className="muted py-8 text-center">Loading…</p>}

      {empty && (
        <section className="card py-8 text-center">
          <p className="text-lg font-semibold">No requests in this time window yet</p>
          <p className="muted mt-1">Ask a question or upload a document, then come back to see how it went.</p>
          <button type="button" className="btn-primary mt-4" onClick={onGoChat}>Go to Chat</button>
        </section>
      )}

      {summary && !empty && (
        <>
          <section aria-label="Key numbers" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Questions" value={formatCount(q.count)} note={`${q.answered} answered · ${q.refused} refused`} />
            <Stat label="Error rate" value={formatPercent(summary.requests.error_rate)}
              note={`${summary.requests.server_errors} of ${summary.requests.total} requests failed`} />
            <Stat label="Latency p50 / p95" value={`${formatMs(q.latency_ms.p50)} / ${formatMs(q.latency_ms.p95)}`} note={`${q.latency_ms.count} questions served`} />
            <Stat label="Refusal rate" value={formatPercent(q.refusal_rate)}
              note={Object.entries(q.refusal_reasons).map(([k, v]) => `${k} ${v}`).join(" · ") || "no refusals"} />
            <Stat label="Tokens per question" value={formatCount(summary.tokens.per_question)}
              note={`${formatCount(summary.tokens.input)} in · ${formatCount(summary.tokens.output)} out in total`} />
            <Stat label="Cost per question (estimate)" value={formatCost(summary.cost.per_question_usd)} note={`${formatCost(summary.cost.total_usd)} in total · per question served`} />
            <Stat label="Requests" value={formatCount(summary.requests.total)}
              note={Object.entries(summary.requests.by_route).map(([k, v]) => `${k} ${v}`).join(" · ")} />
            <Stat label="Rejected" value={formatCount(summary.requests.rate_limited + summary.requests.client_errors)}
              note={`${summary.requests.rate_limited} rate-limited · ${summary.requests.client_errors} bad requests`} />
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="card" aria-labelledby="steps-title">
              <h3 id="steps-title" className="text-base font-semibold">Where the time goes (questions)</h3>
              <p className="muted mb-3">Median bar, 95th percentile tick, successful steps only. Model screen and embedding run in parallel.</p>
              {summary.steps.ask.length ? <StepBars steps={summary.steps.ask} /> : <p className="muted">No questions yet.</p>}
              {summary.steps.upload.length > 0 && (
                <details className="mt-4">
                  <summary className="min-h-11 cursor-pointer font-medium">Uploads</summary>
                  <div className="mt-2"><StepBars steps={summary.steps.upload} /></div>
                </details>
              )}
            </section>
            <section className="card" aria-labelledby="series-title">
              <h3 id="series-title" className="text-base font-semibold">Requests over time</h3>
              <p className="muted mb-3">Red: requests that ended in a server error.</p>
              <SeriesChart series={summary.series} bucketSeconds={summary.bucket_seconds} />
            </section>
          </div>
        </>
      )}


      {requests.length > 0 && (
        <section className="card" aria-labelledby="recent-title">
          <h3 id="recent-title" className="text-base font-semibold">Recent requests</h3>
          <p className="muted mb-2">Open one to see its trace.</p>
          <ul className="space-y-2">
            {requests.map((r) => (
              <li key={r.request_id}>
                <button type="button" onClick={() => setTraceId(r.request_id)} aria-pressed={traceId === r.request_id}
                  className={`flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl px-3 py-2 text-left text-sm transition hover:bg-indigo-50 dark:hover:bg-slate-800/60 ${traceId === r.request_id ? "bg-indigo-50 dark:bg-slate-800/60" : ""}`}>
                  <span className="muted w-20 shrink-0 tabular-nums">{new Date(r.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                  <span className="w-16 shrink-0 font-medium capitalize">{r.route}</span>
                  <StatusBadge status={r.status} />
                  <span className="tabular-nums">{formatMs(r.latency_ms)}</span>
                  <span className="tabular-nums">{formatCount(r.input_tokens + r.output_tokens)} tokens</span>
                  <span className="tabular-nums">{formatCost(r.cost_usd)}</span>
                  <span className="muted min-w-0 truncate">{r.outcome === "refused" ? `refused: ${r.reason}` : r.error_code || r.outcome}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
