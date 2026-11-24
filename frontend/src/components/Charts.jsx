import { formatMs, stepLabel } from "../lib.js";

// "Where the time goes": for each step a bar for the median (p50) and a tick for the 95th percentile (p95).
export function StepBars({ steps }) {
  const max = Math.max(1, ...steps.map((s) => s.p95 ?? s.p50 ?? 0));
  return (
    <ul className="space-y-3">
      {steps.map((s) => (
        <li key={s.name}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
            <span className="min-w-0 wrap-anywhere" title={s.name}>{stepLabel(s.name)}</span>
            <span className="muted tabular-nums">
              p50 {formatMs(s.p50)} · p95 {formatMs(s.p95)} · {s.count}×{s.errors ? ` · ${s.errors} failed` : ""}
            </span>
          </div>
          <div className="relative mt-1 h-3 rounded-full bg-slate-100 dark:bg-slate-800" role="img"
            aria-label={`${stepLabel(s.name)}: median ${formatMs(s.p50)}, 95th percentile ${formatMs(s.p95)}`}>
            <div className="h-full rounded-full bg-linear-to-r from-indigo-500 to-violet-500" style={{ width: `${Math.max(1, ((s.p50 ?? 0) / max) * 100)}%` }} />
            {s.p95 != null && <div className="absolute top-[-2px] h-[calc(100%+4px)] w-0.5 rounded bg-slate-500 dark:bg-slate-300" style={{ left: `calc(${Math.min(99.5, (s.p95 / max) * 100)}% - 1px)` }} />}
          </div>
        </li>
      ))}
    </ul>
  );
}

// Requests over time as bars; the red part of a bar is the requests that ended in a server error.
export function SeriesChart({ series, bucketSeconds }) {
  const max = Math.max(1, ...series.map((p) => p.requests));
  const w = 600, h = 110, gap = 2;
  const barW = Math.max(2, w / series.length - gap);
  const label = (iso) => {
    const d = new Date(iso);
    return bucketSeconds >= 86400 ? d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  const total = series.reduce((n, p) => n + p.requests, 0);
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-32 w-full" preserveAspectRatio="none" role="img"
        aria-label={`Requests over time, ${total} in total, highest bar ${max}`}>
        <line x1="0" x2={w} y1={h - 0.5} y2={h - 0.5} className="stroke-slate-300 dark:stroke-slate-700" strokeWidth="1" />
        {series.map((p, i) => {
          const x = i * (barW + gap);
          const bh = (p.requests / max) * (h - 6);
          const eh = (p.errors / max) * (h - 6);
          return (
            <g key={p.t}>
              <title>{`${label(p.t)}: ${p.requests} requests, ${p.errors} errors`}</title>
              {p.requests > 0 && <rect x={x} y={h - bh} width={barW} height={bh} rx="2" className="fill-indigo-500" />}
              {p.errors > 0 && <rect x={x} y={h - eh} width={barW} height={eh} rx="2" className="fill-red-500" />}
            </g>
          );
        })}
      </svg>
      <div className="muted mt-1 flex justify-between">
        <span>{series.length ? label(series[0].t) : ""}</span>
        <span>highest bar: {max}</span>
        <span>{series.length ? label(series[series.length - 1].t) : ""}</span>
      </div>
    </div>
  );
}
