import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";

// Shared by the Save Session page (end of a MAVIS flight) and the manual
// Upload page.

const NEW = "__new__";

// Who a flight can be saved for: accounts (an instructor's own trainees, every
// trainee for an administrator, and themselves), or a name that has no account.
export function usePilotOptions(sessions) {
  const { user } = useAuth();
  const [trainees, setTrainees] = useState([]);
  useEffect(() => { api.listPilots().then(setTrainees).catch(() => setTrainees([])); }, []);
  return useMemo(() => {
    const accounts = trainees.map((t) => ({ id: t.id, name: t.full_name || t.username }));
    if (user.role !== "trainee") accounts.push({ id: user.id, name: user.full_name || user.username, me: true });
    const seen = new Set(accounts.map((a) => a.name.toLowerCase()));
    const previous = [];
    for (const s of sessions || []) {
      const n = (s.pilot_name || "").trim();
      if (n && !s.pilot_user_id && !seen.has(n.toLowerCase())) { seen.add(n.toLowerCase()); previous.push(n); }
    }
    previous.sort((a, b) => a.localeCompare(b));
    return { accounts, previous, fixed: user.role === "trainee", me: user };
  }, [trainees, sessions, user]);
}

// value / onChange: {pilot_user_id, pilot_name} -- pilot_user_id null = a typed
// name. A trainee's flights are their own: shown, not chosen.
export function PilotPicker({ value, onChange, options }) {
  const [typing, setTyping] = useState(false);
  const { pilot_user_id: id, pilot_name: name = "" } = value;
  if (options.fixed) {
    return <input value={name || options.me.full_name || options.me.username} disabled aria-label="Pilot" />;
  }
  const previous = options.previous.find((n) => n.toLowerCase() === (name || "").toLowerCase());
  const showInput = typing || (!id && !!name && !previous);
  // its pilot's account isn't one this user can choose (another instructor's trainee)
  const other = id && !options.accounts.some((a) => a.id === id);

  return (
    <div className="pilot-picker">
      <select
        value={showInput ? NEW : id ? `u:${id}` : previous ? `n:${previous}` : ""}
        onChange={(e) => {
          const v = e.target.value;
          setTyping(v === NEW);
          if (v.startsWith("u:")) {
            const a = options.accounts.find((x) => x.id === v.slice(2));
            onChange(a ? { pilot_user_id: a.id, pilot_name: a.name } : value);
          } else onChange({ pilot_user_id: null, pilot_name: v.startsWith("n:") ? v.slice(2) : "" });
        }}
      >
        <option value="">— select pilot —</option>
        {other && <option value={`u:${id}`}>{name}</option>}
        {options.accounts.length > 0 && (
          <optgroup label="Accounts">
            {options.accounts.map((a) => <option key={a.id} value={`u:${a.id}`}>{a.name}{a.me ? " (me)" : ""}</option>)}
          </optgroup>
        )}
        {options.previous.length > 0 && (
          <optgroup label="Previous pilots (no account)">
            {options.previous.map((n) => <option key={n} value={`n:${n}`}>{n}</option>)}
          </optgroup>
        )}
        <option value={NEW}>+ New pilot…</option>
      </select>
      {showInput && (
        <input autoFocus placeholder="Type the pilot's name" value={name || ""}
          onChange={(e) => onChange({ pilot_user_id: null, pilot_name: e.target.value })} />
      )}
    </div>
  );
}

// Link one recording per screen (instrument / OTW) from the backend's
// video folder, with a sync offset. `links` = {instrument: {filename,
// offset_sec}, otw: {...}}; saved by the parent via saveVideoLinks().
const SCREENS = [["instrument", "Instrument screen"], ["otw", "OTW screen"]];
const filesFor = (screen, all) => all.filter((f) => f.toLowerCase().includes(screen === "instrument" ? "instrument" : "otw"));

export function VideoLinker({ files, links, setLinks }) {
  if (!files.length) {
    return (
      <p className="muted">
        No video files found in the backend's video folder. Drop MP4s in there (see <code>video_dir</code> in
        config.py) and reload this page.
      </p>
    );
  }
  return (
    <div className="row">
      {SCREENS.map(([screen, label]) => (
        <div className="col" key={screen}>
          <label>{label}</label>
          <select value={links[screen].filename}
            onChange={(e) => setLinks((v) => ({ ...v, [screen]: { ...v[screen], filename: e.target.value } }))}>
            <option value="">— none —</option>
            {filesFor(screen, files).map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <label style={{ marginTop: 8 }}>Sync offset (sec)</label>
          <input type="number" step="0.1" value={links[screen].offset_sec}
            onChange={(e) => setLinks((v) => ({ ...v, [screen]: { ...v[screen], offset_sec: e.target.value } }))} />
        </div>
      ))}
    </div>
  );
}

export const EMPTY_LINKS = { instrument: { filename: "", offset_sec: 0 }, otw: { filename: "", offset_sec: 0 } };

export async function saveVideoLinks(sessionId, links) {
  for (const [screen] of SCREENS) {
    const { filename, offset_sec } = links[screen];
    if (filename) await api.linkVideo(sessionId, screen, { filename, offset_sec: Number(offset_sec) || 0 });
  }
}
