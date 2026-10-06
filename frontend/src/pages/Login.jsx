import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";

// Sign-in screen -- the first thing anyone sees. Accounts are created by
// an administrator (Accounts page); there's no self sign-up.
export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const from = useLocation().state?.from || "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setBusy(true);
    setErr(null);
    try {
      await login(username.trim(), password);
      nav(from, { replace: true });
    } catch (e2) {
      setErr(e2.status === 401 ? e2.detail : "Couldn't reach the server. Is the IRIS backend running?");
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login__card" onSubmit={submit}>
        <div className="login__brand">
          <span className="login__mark">✈</span>
          <div>
            <h1>Flight Review System</h1>
            <p>MAVIS · IRIS</p>
          </div>
        </div>
        <h2>Sign in</h2>
        <label htmlFor="login-user">Username</label>
        <input id="login-user" autoFocus autoComplete="username" value={username}
          onChange={(e) => setUsername(e.target.value)} />
        <label htmlFor="login-pass">Password</label>
        <input id="login-pass" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} />
        {err && <div className="login__err" role="alert">{err}</div>}
        <button type="submit" className="login__btn" disabled={busy || !username.trim() || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="login__hint">Don't have an account? Ask your administrator.</p>
      </form>
    </div>
  );
}
