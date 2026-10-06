import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";

// Shared by the Save Session page (end of a MAVIS flight) and the manual
// Upload page.

const NEW = "__new__";

// Existing pilots = trainee accounts + any pilot name already used on a
// session. Pick one from the dropdown, or choose "+ New pilot…" to type one.
export function usePilotOptions(sessions) {
  const [trainees, setTrainees] = useState([]);
  useEffect(() => { api.listPilots().then(setTrainees).catch(() => setTrainees([])); }, []);
  return useMemo(() => {
    const accounts = trainees.map((t) => t.full_name || t.username);
    const seen = new Set(accounts.map((n) => n.toLowerCase()));
    const previous = [];
    for (const s of sessions || []) {
      const n = (s.pilot_name || "").trim();
      if (n && !seen.has(n.toLowerCase())) { seen.add(n.toLowerCase()); previous.push(n); }
    }
    previous.sort((a, b) => a.localeCompare(b));
    return { accounts, previous };
  }, [trainees, sessions]);
}

export function PilotPicker({ value, onChange, options }) {
  const all = [...options.accounts, ...options.previous];
  const known = !value || all.some((n) => n.toLowerCase() === value.toLowerCase());
  const [typing, setTyping] = useState(false);
  const showInput = typing || !known;

  return (
    <div className="pilot-picker">
      <select
        value={showInput ? NEW : (all.find((n) => n.toLowerCase() === (value || "").toLowerCase()) || "")}
        onChange={(e) => {
          if (e.target.value === NEW) { setTyping(true); onChange(""); }
          else { setTyping(false); onChange(e.target.value); }
        }}
      >
        <option value="">— select pilot —</option>
        {options.accounts.length > 0 && (
          <optgroup label="Trainee accounts">
            {options.accounts.map((n) => <option key={"a" + n} value={n}>{n}</option>)}
          </optgroup>
        )}
        {options.previous.length > 0 && (
          <optgroup label="Previous pilots">
            {options.previous.map((n) => <option key={"p" + n} value={n}>{n}</option>)}
          </optgroup>
        )}
        <option value={NEW}>+ New pilot…</option>
      </select>
      {showInput && (
        <input autoFocus placeholder="Type the pilot's name" value={value || ""}
          onChange={(e) => onChange(e.target.value)} />
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
