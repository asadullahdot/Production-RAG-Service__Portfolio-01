import { useCallback, useEffect, useRef, useState } from "react";
import { api, loadSession, saveSession, setUnauthorizedHandler } from "./api.js";
import AuthForm from "./components/AuthForm.jsx";
import Documents from "./components/Documents.jsx";
import Chat from "./components/Chat.jsx";
import Monitoring from "./components/Monitoring.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import Logo from "./components/Logo.jsx";
import { NAME, SUBTITLE } from "./brand.js";

export default function App() {
  const [boot, setBoot] = useState({ loading: true, authEnabled: false, fileTypes: [], offline: false });
  const [session, setSession] = useState(loadSession);
  const [notices, setNotices] = useState([]);
  const nextId = useRef(1);
  const [attempt, setAttempt] = useState(0);  // bumped by "Try again" to repeat the start-up check

  // Errors stay until dismissed; success messages fade after a few seconds.
  const notify = useCallback((kind, text) => {
    const id = nextId.current++;
    setNotices((list) => [...list.slice(-2), { id, kind, text }]);
    if (kind !== "error") setTimeout(() => setNotices((list) => list.filter((n) => n.id !== id)), 6000);
  }, []);
  const dismiss = (id) => setNotices((list) => list.filter((n) => n.id !== id));

  const logout = useCallback((message) => {
    saveSession(null, "");
    setSession({ token: null, email: "" });
    if (typeof message === "string") notify("info", message);
  }, [notify]);

  // A 401 anywhere (an expired login) sends the user back to the sign-in form.
  useEffect(() => setUnauthorizedHandler(() => logout("Your session expired. Please sign in again.")), [logout]);

  useEffect(() => {
    (async () => {
      let authEnabled = false, fileTypes = [], offline = false;
      try {
        const body = await (await fetch("/health")).json();
        authEnabled = body.auth_enabled === true;
        fileTypes = body.file_types || [];
      } catch {
        offline = true;
      }
      if (authEnabled && session.token) {  // a stored login may have expired: api() signs out on 401
        try { const me = await api("/auth/me", { token: session.token }); setSession((s) => ({ ...s, email: me.email })); } catch { /* handled */ }
      }
      setBoot({ loading: false, authEnabled, fileTypes, offline });
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const signedIn = !boot.authEnabled || !!session.token;

  return (
    <div className="min-h-dvh">
      <div className="px-4 pt-3">
        <header className="mx-auto flex max-w-6xl items-center justify-between gap-3 rounded-3xl border border-slate-200/60 bg-white/80 px-3 py-2 shadow-xl shadow-indigo-500/10 backdrop-blur dark:border-slate-800/60 dark:bg-slate-900/80 sm:px-4">
          <h1 className="flex min-w-0 items-center gap-3">
            <Logo className="size-9 max-[359px]:hidden" />
            <span className="min-w-0">
              <span className="block truncate text-base font-bold leading-tight tracking-[0.1em] sm:text-lg sm:tracking-[0.14em]">{NAME}</span>
              <span className="hidden text-xs text-slate-500 dark:text-slate-400 sm:block">{SUBTITLE}</span>
            </span>
          </h1>
          <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
            <ThemeToggle />
            {boot.authEnabled && session.token && (
              <>
                <span className="muted hidden max-w-[16rem] truncate sm:inline" title={session.email}>{session.email}</span>
                <button type="button" className="btn-ghost" onClick={() => logout()}>Log out</button>
              </>
            )}
          </div>
        </header>
      </div>

      <main className="mx-auto max-w-6xl px-4 py-4">
        <div className="mb-3 space-y-2" aria-live="polite">
          {notices.map((n) => <Notice key={n.id} kind={n.kind} onClose={() => dismiss(n.id)}>{n.text}</Notice>)}
        </div>

        {boot.loading ? (
          <p className="muted py-10 text-center">Loading…</p>
        ) : boot.offline ? (
          <section role="alert" className="card mx-auto mt-6 max-w-md text-center">
            <p className="text-lg font-semibold">Cannot reach the server</p>
            <p className="muted mt-1">Check your connection, or that the service is running, then try again.</p>
            <button type="button" className="btn-primary mt-4" onClick={() => { setBoot((b) => ({ ...b, loading: true })); setAttempt((n) => n + 1); }}>Try again</button>
          </section>
        ) : !signedIn ? (
          <AuthForm onSignedIn={(token, email) => { saveSession(token, email); setSession({ token, email }); }} />
        ) : (
          <Workspace token={session.token} fileTypes={boot.fileTypes} notify={notify} />
        )}
      </main>
    </div>
  );
}

function Notice({ kind, onClose, children }) {
  const style = {
    error: "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-200",
    ok: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
    info: "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200",
  }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`flex items-start justify-between gap-3 rounded-2xl border px-4 py-3 ${style}`}>
      <p className="min-w-0 wrap-anywhere">{children}</p>
      {onClose && <button type="button" onClick={onClose} aria-label="Dismiss message" className="-my-1 -mr-2 min-h-11 min-w-11 text-xl leading-none">×</button>}
    </div>
  );
}

// Signed in: the document list (left on wide screens, on top on phones) and the conversation.
function Workspace({ token, fileTypes, notify }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [excluded, setExcluded] = useState(() => new Set());  // documents the user switched off for questions
  // How much of the per-user limits (documents, chunks, daily tokens) is used, from GET /usage. Refreshed after every upload, delete
  // and question. The UI only reflects it (disabled controls, hints); the server enforces the limits.
  const [usage, setUsage] = useState(null);

  const loadUsage = useCallback(async () => {
    try { setUsage(await api("/usage", { token })); } catch { /* the limits are only a convenience in the UI; the server enforces them */ }
  }, [token]);

  const loadDocs = useCallback(async () => {
    try {
      const data = await api("/documents", { token });
      setDocs(data.documents);
      setExcluded((old) => new Set([...old].filter((id) => data.documents.some((d) => d.doc_id === id))));
      loadUsage();
    } catch (e) {
      notify("error", e.message);
    } finally {
      setLoading(false);
    }
  }, [token, notify, loadUsage]);
  useEffect(() => { loadDocs(); }, [loadDocs]);

  const toggle = (id) => setExcluded((old) => { const s = new Set(old); s.has(id) ? s.delete(id) : s.add(id); return s; });

  const [view, setView] = useState("chat");  // "chat" | "monitoring"
  const [traceRequest, setTraceRequest] = useState(null);  // a new object per click, so the same trace can be opened again
  const openTrace = (id) => { setTraceRequest({ id }); setView("monitoring"); };

  return (
    <>
      <div role="tablist" aria-label="View" className="mx-auto mb-4 flex w-fit gap-1 rounded-full bg-white/70 p-1 shadow-md shadow-indigo-500/10 dark:bg-slate-900/70">
        {[["chat", "Chat"], ["monitoring", "Monitoring"]].map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={view === value} onClick={() => setView(value)}
            className={`min-h-11 rounded-full px-5 font-medium transition ${view === value ? "bg-linear-to-br from-indigo-600 to-violet-600 text-white shadow-md shadow-indigo-500/30 dark:from-indigo-500 dark:to-violet-500" : "text-slate-600 dark:text-slate-300"}`}>
            {label}
          </button>
        ))}
      </div>
      {/* Chat stays mounted (hidden) while Monitoring is open, so the conversation is not lost. */}
      <div className={view === "chat" ? "grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[22rem_minmax(0,1fr)] md:items-start" : "hidden"}>
        <Documents usage={usage} docs={docs} loading={loading} fileTypes={fileTypes} token={token} excluded={excluded} onToggle={toggle} onChanged={loadDocs} notify={notify} />
        <Chat usage={usage} onAnswered={loadUsage} docs={docs} loading={loading} excluded={excluded} token={token} onOpenTrace={openTrace} />
      </div>
      {view === "monitoring" && <Monitoring token={token} initialTrace={traceRequest} onGoChat={() => setView("chat")} />}
    </>
  );
}
