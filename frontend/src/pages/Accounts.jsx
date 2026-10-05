import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { ROLE_LABELS, RoleTag, useAuth } from "../auth/AuthContext.jsx";

// Administrator-only: create, edit and remove accounts. A trainee gets one
// instructor (dropdown); an instructor can have any number of trainees.
const who = (u) => u.full_name || u.username;
const EMPTY = { username: "", full_name: "", email: "", role: "trainee", password: "", instructor_id: "" };

function UserForm({ initial, instructors, isNew, onSave, onCancel }) {
  const [f, setF] = useState(initial);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await onSave(f);
    } catch (e2) {
      setErr(e2.detail || e2.message);
      setBusy(false);
    }
  }

  return (
    <div className="set-backdrop acct-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <form className="acct-modal" onSubmit={submit}>
        <h2>{isNew ? "New account" : `Edit ${who(initial)}`}</h2>
        <div className="acct-modal__grid">
          <div>
            <label>Username</label>
            <input value={f.username} onChange={set("username")} disabled={!isNew} autoFocus={isNew} required />
          </div>
          <div>
            <label>Role</label>
            <select value={f.role} onChange={set("role")}>
              {Object.entries(ROLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label>Full name</label>
            <input value={f.full_name} onChange={set("full_name")} />
          </div>
          <div>
            <label>Email</label>
            <input type="email" value={f.email} onChange={set("email")} placeholder="optional" />
          </div>
          {f.role === "trainee" && (
            <div className="acct-modal__wide">
              <label>Instructor</label>
              <select value={f.instructor_id} onChange={set("instructor_id")}>
                <option value="">— none —</option>
                {instructors.map((i) => <option key={i.id} value={i.id}>{who(i)} (@{i.username})</option>)}
              </select>
              {!instructors.length && <p className="set-help">Create an Instructor account first to assign one.</p>}
            </div>
          )}
          <div className="acct-modal__wide">
            <label>{isNew ? "Password" : "Reset password"}</label>
            <input type="password" autoComplete="new-password" value={f.password} onChange={set("password")}
              placeholder={isNew ? "" : "leave blank to keep the current password"} required={isNew} />
          </div>
        </div>
        {err && <div className="acct-err acct-modal__err">{err}</div>}
        <div className="acct-actions">
          <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={busy}>{busy ? "Saving…" : isNew ? "Create account" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}

export default function Accounts() {
  const { user: me, refresh } = useAuth();
  const [users, setUsers] = useState(null);
  const [err, setErr] = useState(null);
  const [editing, setEditing] = useState(null); // null | "new" | user
  const [filter, setFilter] = useState("all");

  const load = () => api.listUsers().then(setUsers).catch((e) => setErr(e.detail || e.message));
  useEffect(() => { load(); }, []);

  const instructors = useMemo(() => (users || []).filter((u) => u.role === "instructor"), [users]);
  const shown = (users || []).filter((u) => filter === "all" || u.role === filter);
  const counts = (users || []).reduce((c, u) => ({ ...c, [u.role]: (c[u.role] || 0) + 1 }), {});

  async function save(f) {
    const body = { full_name: f.full_name.trim(), email: f.email.trim(), role: f.role };
    if (f.role === "trainee") {
      if (f.instructor_id) body.instructor_id = f.instructor_id;
      else body.clear_instructor = true;
    }
    if (editing === "new") {
      await api.createUser({ ...body, username: f.username.trim(), password: f.password,
        instructor_id: f.role === "trainee" && f.instructor_id ? f.instructor_id : null });
    } else {
      if (f.password) body.password = f.password;
      await api.updateUser(editing.id, body);
      if (editing.id === me.id) refresh();
    }
    setEditing(null);
    load();
  }

  async function remove(u) {
    if (!window.confirm(`Remove ${who(u)} (@${u.username})? This can't be undone.`)) return;
    try {
      await api.deleteUser(u.id);
      load();
    } catch (e) {
      alert(e.detail || e.message);
    }
  }

  return (
    <div className="acct-page">
      <header className="acct-head">
        <div>
          <h1>Accounts</h1>
          <p className="muted">Create and remove accounts, set roles, and assign each trainee an instructor.</p>
        </div>
        <button type="button" onClick={() => setEditing("new")}>+ New account</button>
      </header>

      <div className="mg-seg acct-filter">
        {[["all", `All (${(users || []).length})`], ...Object.entries(ROLE_LABELS).map(([v, l]) => [v, `${l}s (${counts[v] || 0})`])]
          .map(([v, l]) => (
            <button key={v} type="button" className={filter === v ? "is-on" : ""} onClick={() => setFilter(v)}>{l}</button>
          ))}
      </div>

      {err && <div className="acct-err">{err}</div>}
      {!users ? <p className="muted">Loading…</p> : (
        <div className="acct-card acct-table-card">
          <table className="acct-table">
            <thead>
              <tr><th>Name</th><th>Username</th><th>Role</th><th>Instructor / Trainees</th><th>Email</th><th /></tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.id}>
                  <td><b>{who(u)}</b>{u.id === me.id && <span className="acct-you">you</span>}</td>
                  <td className="muted">@{u.username}</td>
                  <td><RoleTag role={u.role} small /></td>
                  <td>
                    {u.role === "trainee" && (u.instructor ? who(u.instructor) : <span className="muted">unassigned</span>)}
                    {u.role === "instructor" && (u.trainees.length
                      ? <span title={u.trainees.map(who).join(", ")}>{u.trainees.length} trainee{u.trainees.length > 1 ? "s" : ""}</span>
                      : <span className="muted">no trainees</span>)}
                  </td>
                  <td className="muted">{u.email || "—"}</td>
                  <td className="acct-row-actions">
                    <button type="button" className="secondary" onClick={() => setEditing(u)}>Edit</button>
                    <button type="button" className="secondary acct-danger" disabled={u.id === me.id}
                      title={u.id === me.id ? "You can't remove your own account" : "Remove account"}
                      onClick={() => remove(u)}>Remove</button>
                  </td>
                </tr>
              ))}
              {!shown.length && <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 20 }}>No accounts here.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <UserForm
          isNew={editing === "new"}
          instructors={instructors.filter((i) => editing === "new" || i.id !== editing.id)}
          initial={editing === "new" ? EMPTY : {
            username: editing.username, full_name: editing.full_name || "", email: editing.email || "",
            role: editing.role, password: "", instructor_id: editing.instructor_id || "",
          }}
          onSave={save}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}
