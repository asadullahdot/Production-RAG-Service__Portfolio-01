// Plain-JS frontend. All server-provided text is rendered with textContent / text nodes only (no innerHTML).
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { token: null, authEnabled: false, email: "" };

  try { state.token = localStorage.getItem("token"); state.email = localStorage.getItem("email") || ""; } catch (_) {}

  function saveSession(token, email) {
    state.token = token; state.email = email;
    try { token ? (localStorage.setItem("token", token), localStorage.setItem("email", email))
                : (localStorage.removeItem("token"), localStorage.removeItem("email")); } catch (_) {}
  }

  function showError(msg) { const e = $("error"); e.textContent = msg || ""; e.hidden = !msg; }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    let res;
    try { res = await fetch(path, Object.assign({}, opts, { headers })); }
    catch (_) { throw new Error("Cannot reach the server. Check your connection."); }
    if (res.status === 401 && state.authEnabled) { saveSession(null, ""); render(); }
    if (res.status === 204) return null;
    let body = null;
    try { body = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = body && body.error ? body.error : {};
      const e = new Error(err.message || ("Request failed (" + res.status + ")"));
      e.code = err.code; e.status = res.status; e.details = err.details;
      throw e;
    }
    return body;
  }

  function render() {
    const loggedIn = !state.authEnabled || !!state.token;
    $("auth").hidden = loggedIn;
    $("app").hidden = !loggedIn;
    $("session").hidden = !(state.authEnabled && state.token);
    $("whoami").textContent = state.email;
    if (loggedIn) loadDocs();
  }

  // ---------------------------------------------------------------- auth (B1)
  async function authenticate(signup) {
    showError("");
    const email = $("email").value.trim(), password = $("password").value;
    try {
      if (signup) await api("/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ email, password }) });
      const form = new URLSearchParams({ username: email, password });
      const tok = await api("/auth/login", { method: "POST", body: form });
      saveSession(tok.access_token, email);
      $("password").value = "";
      render();
    } catch (e) { showError(e.message); }
  }
  $("auth-form").addEventListener("submit", (ev) => { ev.preventDefault(); authenticate(false); });
  $("signup-btn").addEventListener("click", () => { if ($("auth-form").reportValidity()) authenticate(true); });
  $("logout").addEventListener("click", () => { saveSession(null, ""); render(); });

  // ---------------------------------------------------------------- documents
  async function loadDocs() {
    try {
      const data = await api("/documents");
      const list = $("doc-list");
      list.replaceChildren();
      for (const d of data.documents) {
        const li = document.createElement("li");
        const info = document.createElement("div");
        info.className = "name";
        const title = document.createElement("strong"); title.textContent = d.file_name;
        const meta = document.createElement("div"); meta.className = "muted";
        meta.textContent = d.file_type.toUpperCase() + " · " + d.num_chunks + " chunks · " + d.uploaded_at.slice(0, 10);
        info.append(title, meta);
        const del = document.createElement("button");
        del.type = "button"; del.className = "danger"; del.textContent = "Delete";
        del.setAttribute("aria-label", "Delete " + d.file_name);
        del.addEventListener("click", () => removeDoc(d, del));
        li.append(info, del);
        list.append(li);
      }
      $("doc-empty").hidden = data.documents.length > 0;
    } catch (e) { showError(e.message); }
  }

  async function removeDoc(d, btn) {
    showError(""); btn.disabled = true;
    try { await api("/documents/" + encodeURIComponent(d.doc_id), { method: "DELETE" }); await loadDocs(); }
    catch (e) { showError(e.message); btn.disabled = false; }
  }

  $("upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); showError("");
    const file = $("file").files[0]; if (!file) return;
    const btn = $("upload-btn"); btn.disabled = true;
    $("upload-status").textContent = "Uploading and indexing…";
    try {
      const fd = new FormData(); fd.append("file", file);
      const doc = await api("/documents", { method: "POST", body: fd });
      $("upload-status").textContent = "Added " + doc.file_name + " (" + doc.num_chunks + " chunks).";
      $("file").value = "";
      await loadDocs();
    } catch (e) {
      $("upload-status").textContent = "";
      showError(e.message);
    } finally { btn.disabled = false; }
  });

  // ---------------------------------------------------------------- ask
  function renderAnswer(text, citations) {
    const out = $("answer"); out.replaceChildren();
    const valid = new Set(citations.map((c) => c.n));
    for (const part of text.split(/(\[\d+\])/)) {
      const m = /^\[(\d+)\]$/.exec(part);
      if (m && valid.has(Number(m[1]))) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "cite"; b.textContent = part;
        b.setAttribute("aria-label", "Show source " + m[1]);
        b.addEventListener("click", () => focusChunk(Number(m[1])));
        out.append(b);
      } else out.append(document.createTextNode(part));
    }
  }

  function focusChunk(n) {
    $("chunks-panel").open = true;
    const li = $("chunk-" + n); if (!li) return;
    li.scrollIntoView({ behavior: "smooth", block: "center" });
    li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1500);
  }

  function renderChunks(chunks) {
    $("chunks-summary").textContent = "Retrieved chunks (" + chunks.length + ")";
    const ol = $("chunks"); ol.replaceChildren();
    for (const c of chunks) {
      const li = document.createElement("li"); li.id = "chunk-" + c.n; li.value = c.n;
      const src = document.createElement("div"); src.className = "src";
      src.textContent = c.file_name + " · page/section " + c.page + " · similarity " + c.score.toFixed(3);
      const text = document.createElement("div"); text.className = "text"; text.textContent = c.text;
      li.append(src, text); ol.append(li);
    }
  }

  const REASONS = {
    no_documents: "No documents uploaded yet.", no_relevant_context: "Nothing in your documents was relevant enough to this question.",
    not_in_documents: "Your documents do not contain the answer.", prompt_injection: "This request tries to change the assistant's rules.",
    jailbreak: "This request tries to bypass the assistant's rules.", output_blocked: "The answer was blocked by a safety check.",
  };

  $("ask-form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); showError("");
    const q = $("question").value.trim(); if (!q) return;
    const btn = $("ask-btn"); btn.disabled = true; $("ask-status").textContent = "Thinking…"; $("result").hidden = true;
    try {
      const r = await api("/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const refusal = $("refusal");
      refusal.hidden = !r.refused;
      refusal.textContent = r.refused ? "Refused: " + (REASONS[r.reason] || r.reason) + " (" + r.reason + ")" : "";
      renderAnswer(r.answer, r.citations);
      $("meta").textContent = r.num_retrieved + " chunks retrieved · " + r.latency_ms + " ms · "
        + (r.usage.input_tokens + r.usage.output_tokens) + " tokens";
      renderChunks(r.retrieved_chunks);
      $("result").hidden = false;
    } catch (e) { showError(e.message); }
    finally { btn.disabled = false; $("ask-status").textContent = ""; }
  });

  // ---------------------------------------------------------------- start
  (async () => {
    try { const h = await fetch("/health"); state.authEnabled = (await h.json()).auth_enabled === true; } catch (_) {}
    render();
  })();
})();
