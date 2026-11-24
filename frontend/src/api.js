// The only place that talks to the server. Errors carry the server's own message (error.message in the JSON body).
const TOKEN = "token";
const EMAIL = "email";

export function loadSession() {
  try {
    return { token: localStorage.getItem(TOKEN), email: localStorage.getItem(EMAIL) || "" };
  } catch {
    return { token: null, email: "" };
  }
}

export function saveSession(token, email) {
  try {
    if (token) {
      localStorage.setItem(TOKEN, token);
      localStorage.setItem(EMAIL, email);
    } else {
      localStorage.removeItem(TOKEN);
      localStorage.removeItem(EMAIL);
    }
  } catch {
    /* storage can be blocked (private window); the app then simply asks for the login again next time */
  }
}

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export async function api(path, { token, ...opts } = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = "Bearer " + token;
  let res;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0, "network");
  }
  if (res.status === 401 && token) onUnauthorized();
  if (res.status === 204) return null;
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  if (!res.ok) {
    const err = body && body.error ? body.error : {};
    throw new ApiError(err.message || `Something went wrong (${res.status}).`, res.status, err.code);
  }
  return body;
}

export const json = (data) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
