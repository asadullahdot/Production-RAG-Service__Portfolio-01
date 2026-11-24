import { useCallback, useEffect, useRef, useState } from "react";
import { api, loadSession, saveSession, setUnauthorizedHandler } from "./api.js";
import AuthForm from "./components/AuthForm.jsx";
import Documents from "./components/Documents.jsx";
import Chat from "./components/Chat.jsx";

export default function App() {
  const [boot, setBoot] = useState({ loading: true, authEnabled: false, fileTypes: [], offline: false });
  const [session, setSession] = useState(loadSession);
  const [notices, setNotices] = useState([]);
  const nextId = useRef(1);

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
  }, []);

  const signedIn = !boot.authEnabled || !!session.token;

  return (
    <div className="min-h-dvh">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="size-6 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" />
            </svg>
            Document Q&amp;A
          </h1>
          {boot.authEnabled && session.token && (
            <div className="flex items-center gap-3">
              <span className="muted max-w-[16rem] truncate" title={session.email}>{session.email}</span>
              <button type="button" className="btn-ghost" onClick={() => logout()}>Log out</button>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-4">
        <div className="mb-3 space-y-2" aria-live="polite">
          {boot.offline && <Notice kind="error">Cannot reach the server. Check that it is running, then reload the page.</Notice>}
          {notices.map((n) => <Notice key={n.id} kind={n.kind} onClose={() => dismiss(n.id)}>{n.text}</Notice>)}
        </div>

        {boot.loading ? (
          <p className="muted py-10 text-center">Loading…</p>
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
    <div role={kind === "error" ? "alert" : "status"} className={`flex items-start justify-between gap-3 rounded-xl border px-4 py-3 ${style}`}>
      <p className="min-w-0 break-words">{children}</p>
      {onClose && <button type="button" onClick={onClose} aria-label="Dismiss message" className="-my-1 -mr-2 min-h-11 min-w-11 text-xl leading-none">×</button>}
    </div>
  );
}

// Signed in: the document list (left on wide screens, on top on phones) and the conversation.
function Workspace({ token, fileTypes, notify }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [excluded, setExcluded] = useState(() => new Set());  // documents the user switched off for questions

  const loadDocs = useCallback(async () => {
    try {
      const data = await api("/documents", { token });
      setDocs(data.documents);
      setExcluded((old) => new Set([...old].filter((id) => data.documents.some((d) => d.doc_id === id))));
    } catch (e) {
      notify("error", e.message);
    } finally {
      setLoading(false);
    }
  }, [token, notify]);
  useEffect(() => { loadDocs(); }, [loadDocs]);

  const toggle = (id) => setExcluded((old) => { const s = new Set(old); s.has(id) ? s.delete(id) : s.add(id); return s; });

  return (
    <div className="grid gap-4 md:grid-cols-[22rem_minmax(0,1fr)] md:items-start">
      <Documents docs={docs} loading={loading} fileTypes={fileTypes} token={token} excluded={excluded} onToggle={toggle} onChanged={loadDocs} notify={notify} />
      <Chat docs={docs} loading={loading} excluded={excluded} token={token} />
    </div>
  );
}
