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

// Which documents to send with a question. Everything included (or nothing excluded) means "all", so send nothing.
export function scopeIds(docs, excluded) {
  const included = docs.filter((d) => !excluded.has(d.doc_id)).map((d) => d.doc_id);
  return included.length === docs.length ? undefined : included;
}

// Enter sends, Shift+Enter makes a new line, and Enter while an IME is composing text (Japanese, Chinese...) never sends.
export function isSendKey(e) {
  return e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229;
}
