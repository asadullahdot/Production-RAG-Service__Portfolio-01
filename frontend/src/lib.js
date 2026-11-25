// Small pure helpers (unit-tested in lib.test.js). No DOM access here.

// What "page" means depends on the file type: a PDF page, a PowerPoint slide, a heading section in Word and Markdown.
export function unitLabel(fileName) {
  const ext = String(fileName).split(".").pop().toLowerCase();
  if (ext === "pdf") return "page";
  if (ext === "pptx") return "slide";
  if (ext === "txt") return "text";
  return "section";
}

// Turn the answer into text and citation pieces. Only [n] markers the server confirmed (present in `citations`) become
// citation chips; anything else, including a stray "[7]", stays plain text. Output is data, never HTML.
export function splitAnswer(text, citations) {
  const valid = new Set((citations || []).map((c) => c.n));
  return String(text ?? "")
    .split(/(\[\d+\])/)
    .filter((part) => part !== "")
    .map((part) => {
      const m = /^\[(\d+)\]$/.exec(part);
      return m && valid.has(Number(m[1])) ? { type: "cite", n: Number(m[1]), text: part } : { type: "text", text: part };
    });
}

export const REASONS = {
  no_documents: "There are no documents yet. Upload one first.",
  no_relevant_context: "Nothing in your documents was close enough to this question.",
  not_in_documents: "Your documents do not contain the answer.",
  prompt_injection: "This request tries to change the assistant's rules.",
  jailbreak: "This request tries to bypass the assistant's rules.",
  output_blocked: "The answer was blocked by a safety check.",
};

export function reasonText(reason) {
  return REASONS[reason] || String(reason || "");
}

// "pdf,docx,md" -> ".pdf,.docx,.md,.markdown" for the file picker, and a readable list for the hint.
export function acceptAttribute(types) {
  return (types || []).flatMap((t) => (t === "md" ? [".md", ".markdown"] : ["." + t])).join(",");
}
export function typeList(types) {
  return (types || []).map((t) => t.toUpperCase()).join(", ");
}

export function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 10) : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

// One line shown on a closed Sources panel: the cited sources first (or the top hits if none was cited), at most three.
export function sourcePreview(chunks, citations) {
  const cited = new Set((citations || []).map((c) => c.n));
  const ordered = [...chunks.filter((c) => cited.has(c.n)), ...chunks.filter((c) => !cited.has(c.n))];
  const chosen = cited.size ? ordered.filter((c) => cited.has(c.n)) : ordered;
  const items = [...new Set(chosen.map((c) => `${c.file_name} ${unitLabel(c.file_name)} ${c.page}`))];
  return items.slice(0, 3).join(", ") + (items.length > 3 ? ` +${items.length - 3} more` : "");
}

// Tokens the API reported for the answers of this session (screen + answer model; embeddings are not included).
export function sumTokens(results) {
  return results.reduce((sum, r) => sum + (r?.usage ? r.usage.input_tokens + r.usage.output_tokens : 0), 0);
}

// The theme to show: the user's saved choice if there is one, otherwise whatever the system uses.
export function resolveTheme(stored, systemDark) {
  return stored === "light" || stored === "dark" ? stored : systemDark ? "dark" : "light";
}

// Which documents to send with a question. Everything included (or nothing excluded) means "all", so send nothing.
export function scopeIds(docs, excluded) {
  const included = docs.filter((d) => !excluded.has(d.doc_id)).map((d) => d.doc_id);
  return included.length === docs.length ? undefined : included;
}

// Enter sends, Shift+Enter makes a new line, and Enter while an IME is composing text (Japanese, Chinese...) never sends.
export function isSendKey(e) {
  return e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229;
}

// ---- monitoring (B3) ---------------------------------------------------------------------------------------------
export function formatMs(ms) {
  if (ms == null) return "–";
  if (ms >= 1000) return (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + " s";
  return Math.round(ms) + " ms";
}

export function formatPercent(x) {
  return x == null ? "–" : (x * 100).toFixed(x > 0 && x < 0.01 ? 2 : 1) + "%";
}

export function formatCost(usd) {
  if (usd == null) return "–";
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return "$" + usd.toFixed(usd < 0.01 ? 4 : 2);
}

export function formatCount(n) {
  return n == null ? "–" : Math.round(n).toLocaleString();
}

export function statusTone(status) {
  return status >= 500 ? "bad" : status >= 400 ? "warn" : "ok";
}

const STEP_LABELS = {
  request: "Whole request", input_guardrails: "Input guardrails", recheck: "Model recheck", registry_check: "Document registry",
  screen: "Model screen", embed: "Embed question", vector_search: "Vector search", registry_filter: "Registry filter",
  answer: "Answer", "llm.generate": "Model call", output_guardrails: "Output guardrails", citations: "Citation check",
  validate: "Validate file", extract: "Extract text", chunk: "Split into chunks", embed_chunks: "Embed chunks",
  vector_upsert: "Store vectors", registry_write: "Write registry", vector_delete: "Delete vectors", registry_delete: "Delete registry row",
};
export const stepLabel = (name) => STEP_LABELS[name] || name;

// Where each bar of the waterfall sits, in percent of the whole request. Every bar stays visible and inside the track.
export function waterfallGeometry(spans, total) {
  const whole = total > 0 ? total : 1;
  return spans.map((s) => {
    const left = Math.min(99, Math.max(0, (s.offset_ms / whole) * 100));
    const width = Math.max(0.8, Math.min(100 - left, (s.duration_ms / whole) * 100));
    return { left, width };
  });
}

// A short, readable summary of a span's attributes for the trace view.
export function spanDetail(attrs = {}) {
  const parts = [];
  if (attrs.model) parts.push(attrs.model);
  if ("tokens.input" in attrs) parts.push(`${attrs["tokens.input"]} → ${attrs["tokens.output"] ?? 0} tokens`);
  if (attrs.retries) parts.push(`${attrs.retries} ${attrs.retries === 1 ? "retry" : "retries"}`);
  if ("hits" in attrs) parts.push(`${attrs.hits} hits`);
  if (attrs.dropped) parts.push(`${attrs.dropped} dropped`);
  if ("chunks" in attrs) parts.push(`${attrs.chunks} chunks`);
  if ("pages" in attrs) parts.push(`${attrs.pages} pages`);
  if (attrs.blocked === true) parts.push("blocked");
  if (attrs["error.type"]) parts.push(`error: ${attrs["error.type"]}`);
  return parts.join(" · ");
}

// ---- per-user limits (a limit of 0 means no limit) ----------------------------------------------------------------
export const isFull = (l) => !!l && l.limit > 0 && l.used >= l.limit;

export function limitText(l) {
  if (!l) return "";
  return l.limit > 0 ? `${l.used.toLocaleString()} of ${l.limit.toLocaleString()}` : l.used.toLocaleString();
}

export function formatResetTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "00:00 UTC" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

