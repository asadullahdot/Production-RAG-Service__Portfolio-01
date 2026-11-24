import { useState } from "react";
import { api, json } from "../api.js";

export default function AuthForm({ onSignedIn }) {
  const [mode, setMode] = useState("login");  // "login" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const signup = mode === "signup";

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const address = email.trim();
      if (signup) await api("/auth/signup", json({ email: address, password }));
      const tok = await api("/auth/login", { method: "POST", body: new URLSearchParams({ username: address, password }) });
      onSignedIn(tok.access_token, address);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  const tab = (value, label) => (
    <button type="button" role="tab" aria-selected={mode === value} onClick={() => { setMode(value); setError(""); }}
      className={`min-h-11 flex-1 rounded-lg px-3 font-medium ${mode === value ? "bg-white shadow-sm dark:bg-slate-800" : "text-slate-500 dark:text-slate-400"}`}>
      {label}
    </button>
  );

  return (
    <section className="card mx-auto mt-6 max-w-md" aria-labelledby="auth-title">
      <h2 id="auth-title" className="text-xl font-semibold">{signup ? "Create your account" : "Welcome back"}</h2>
      <p className="muted mt-1">{signup ? "Your documents stay private to your account." : "Sign in to ask questions about your documents."}</p>

      <div role="tablist" aria-label="Sign in or sign up" className="mt-4 flex gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-950">
        {tab("login", "Log in")}
        {tab("signup", "Sign up")}
      </div>

      <form onSubmit={submit} className="mt-4 space-y-4">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Email</span>
          <input className="field" type="email" required autoComplete="username" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Password</span>
          <span className="flex gap-2">
            <input className="field" type={show ? "text" : "password"} required minLength={8} maxLength={128}
              autoComplete={signup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="btn-ghost shrink-0" onClick={() => setShow((v) => !v)} aria-pressed={show}>{show ? "Hide" : "Show"}</button>
          </span>
          {signup && <span className="muted mt-1 block">At least 8 characters.</span>}
        </label>
        {error && <p role="alert" className="rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-200">{error}</p>}
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? (signup ? "Creating account…" : "Signing in…") : signup ? "Create account" : "Log in"}
        </button>
      </form>
    </section>
  );
}
