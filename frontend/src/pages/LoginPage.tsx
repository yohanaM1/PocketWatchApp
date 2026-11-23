import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import type { LoginState } from "../App";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Logo } from "../components/Logo";

/** Where to go after logging in: the page RequireAuth sent us from, else /ask. */
function destination(state: unknown): string {
  const from = (state as LoginState | null)?.from;
  return from && from.pathname !== "/login" ? from.pathname + from.search : "/ask";
}

export function LoginPage() {
  const { token, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = destination(location.state);

  if (token) return <Navigate to={next} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (mode === "register" && password !== confirm) {
      setError("The passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "register") await register(email, password);
      await login(email, password);
      navigate(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError("That email is already registered. Log in instead.");
      else if (err instanceof ApiError && err.status === 422) setError("Use a valid email and a password of at least eight characters.");
      else if (err instanceof ApiError && err.status === 401) setError("Wrong email or password.");
      else setError("Couldn't reach PocketWatch. Is the backend running?");
    } finally {
      setBusy(false);
    }
  }

  function toggleMode() {
    setMode(mode === "login" ? "register" : "login");
    setConfirm("");
    setError(null);
  }

  return (
    <main className="page narrow">
      <header className="login-brand">
        <Logo size={56} />
        <h1>PocketWatch</h1>
        <p className="tagline">See where your money goes, with every number checked against your transactions.</p>
      </header>
      <form className="card stack" onSubmit={submit}>
        <h2>{mode === "login" ? "Log in" : "Create an account"}</h2>
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required
            minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {mode === "register" && (
          <label>
            Confirm password
            <input type="password" autoComplete="new-password" required minLength={8} value={confirm}
              onChange={(e) => setConfirm(e.target.value)} />
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>{mode === "login" ? "Log in" : "Register"}</button>
        <button type="button" className="link" onClick={toggleMode}>
          {mode === "login" ? "New here? Create an account" : "Have an account? Log in"}
        </button>
      </form>
    </main>
  );
}
