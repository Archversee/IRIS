import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { RoleTag, useAuth } from "../auth/AuthContext.jsx";

// Own profile: edit name/email, change password, and see who you're linked
// to (a trainee's instructor, an instructor's trainees).
const who = (u) => u.full_name || u.username;

export default function Profile() {
  const { user, setUser, logout } = useAuth();
  const [fullName, setFullName] = useState(user.full_name || "");
  const [email, setEmail] = useState(user.email || "");
  const [saveMsg, setSaveMsg] = useState(null);
  const [saving, setSaving] = useState(false);

  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [pwMsg, setPwMsg] = useState(null);

  useEffect(() => { setFullName(user.full_name || ""); setEmail(user.email || ""); }, [user.id]);

  const dirty = fullName !== (user.full_name || "") || email !== (user.email || "");

  async function saveDetails(e) {
    e.preventDefault();
    setSaving(true);
    setSaveMsg(null);
    try {
      setUser(await api.updateMe({ full_name: fullName.trim(), email: email.trim() }));
      setSaveMsg({ ok: true, text: "Saved." });
    } catch (e2) {
      setSaveMsg({ ok: false, text: e2.detail || e2.message });
    } finally {
      setSaving(false);
    }
  }

  async function changePassword(e) {
    e.preventDefault();
    if (next !== again) { setPwMsg({ ok: false, text: "New passwords don't match." }); return; }
    try {
      await api.changePassword(cur, next);
      setCur(""); setNext(""); setAgain("");
      setPwMsg({ ok: true, text: "Password changed." });
    } catch (e2) {
      setPwMsg({ ok: false, text: e2.detail || e2.message });
    }
  }

  const initials = (who(user).match(/\b\w/g) || ["?"]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="acct-page">
      <header className="acct-hero">
        <div className="acct-avatar acct-avatar--lg">{initials}</div>
        <div>
          <h1>{who(user)}</h1>
          <div className="acct-hero__meta">
            <RoleTag role={user.role} />
            <span>@{user.username}</span>
            {user.created_at && <span>Member since {new Date(user.created_at).toLocaleDateString()}</span>}
          </div>
        </div>
        <button type="button" className="secondary acct-logout" onClick={logout}>Log out</button>
      </header>

      <div className="acct-grid">
        <form className="acct-card" onSubmit={saveDetails}>
          <h2>Details</h2>
          <label>Full name</label>
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={120} />
          <label>Email</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={200} placeholder="optional" />
          <label>Username</label>
          <input value={user.username} disabled title="Usernames are set by your administrator" />
          <div className="acct-actions">
            {saveMsg && <span className={saveMsg.ok ? "acct-ok" : "acct-err"}>{saveMsg.text}</span>}
            <button type="submit" disabled={!dirty || saving}>{saving ? "Saving…" : "Save changes"}</button>
          </div>
        </form>

        <form className="acct-card" onSubmit={changePassword}>
          <h2>Password</h2>
          <label>Current password</label>
          <input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
          <label>New password</label>
          <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          <label>Confirm new password</label>
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
          <div className="acct-actions">
            {pwMsg && <span className={pwMsg.ok ? "acct-ok" : "acct-err"}>{pwMsg.text}</span>}
            <button type="submit" disabled={!cur || !next || !again}>Change password</button>
          </div>
        </form>

        {user.role === "trainee" && (
          <div className="acct-card">
            <h2>My instructor</h2>
            {user.instructor ? (
              <div className="acct-person">
                <div className="acct-avatar">{(who(user.instructor)[0] || "?").toUpperCase()}</div>
                <div><b>{who(user.instructor)}</b><div className="muted">@{user.instructor.username}</div></div>
                <RoleTag role="instructor" small />
              </div>
            ) : (
              <p className="muted">No instructor assigned yet. Your administrator can assign one.</p>
            )}
          </div>
        )}

        {user.role === "instructor" && (
          <div className="acct-card">
            <h2>My trainees <span className="acct-count">{user.trainees.length}</span></h2>
            {user.trainees.length ? (
              <ul className="acct-people">
                {user.trainees.map((t) => (
                  <li key={t.id} className="acct-person">
                    <div className="acct-avatar">{(who(t)[0] || "?").toUpperCase()}</div>
                    <div><b>{who(t)}</b><div className="muted">@{t.username}</div></div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No trainees assigned to you yet.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
