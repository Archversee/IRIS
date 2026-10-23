import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getTheme, setTheme } from "../theme.js";
import { ChecklistIcon, UploadIcon, TargetIcon } from "./SidebarIcons.jsx";

// Settings drawer, opened from the sidebar's gear. Only Theme is wired up;
// everything else is a placeholder (selectable, but not saved or applied
// anywhere yet) -- marked "Soon". Add new rows under the relevant section.

function Segmented({ options, value, onChange }) {
  return (
    <div className="set-seg" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
          className={value === o.value ? "is-on" : ""} onClick={() => onChange(o.value)}>
          {o.icon && <span className="set-seg__icon" aria-hidden="true">{o.icon}</span>}
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Field({ label, soon, help, children }) {
  return (
    <div className="set-field">
      <div className="set-field__label">
        {label}
        {soon && <span className="set-soon" title="Not wired up yet">Soon</span>}
      </div>
      {children}
      {help && <p className="set-help">{help}</p>}
    </div>
  );
}

export default function SettingsPanel({ onClose }) {
  const [theme, setThemeState] = useState(getTheme);
  // placeholders -- local only
  const [timeFmt, setTimeFmt] = useState("24");
  const [dateFmt, setDateFmt] = useState("DD-MMM-YYYY");
  const [nameFmt, setNameFmt] = useState("given-family");
  const [jargon, setJargon] = useState("layman");
  const [exam, setExam] = useState("off");

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function chooseTheme(t) {
    setThemeState(t);
    setTheme(t);
  }

  return (
    <div className="set-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="set-panel" role="dialog" aria-modal="true" aria-labelledby="set-title">
        <header className="set-head">
          <h2 id="set-title">Settings</h2>
          <button type="button" className="set-close" onClick={onClose} aria-label="Close settings">✕</button>
        </header>

        <div className="set-body">
          <section className="set-section">
            <h3>Display</h3>
            <Field label="Theme">
              <Segmented value={theme} onChange={chooseTheme} options={[
                { value: "dark", label: "Dark", icon: "🌙" },
                { value: "light", label: "Light", icon: "☀️" },
              ]} />
            </Field>
            <Field label="Time format" soon>
              <Segmented value={timeFmt} onChange={setTimeFmt} options={[
                { value: "24", label: "24-hour" },
                { value: "12", label: "12-hour" },
              ]} />
            </Field>
            <Field label="Date format" soon>
              <select className="set-select" value={dateFmt} onChange={(e) => setDateFmt(e.target.value)}>
                <option value="DD-MMM-YYYY">DD-MMM-YYYY — 09-Jul-2026</option>
                <option value="YYYY-MM-DD">YYYY-MM-DD — 2026-07-09</option>
                <option value="DD/MM/YYYY">DD/MM/YYYY — 09/07/2026</option>
                <option value="MM/DD/YYYY">MM/DD/YYYY — 07/09/2026</option>
              </select>
            </Field>
            <Field label="Name format" soon help="Applies to the trainee name on this menu and to chat timestamps.">
              <select className="set-select" value={nameFmt} onChange={(e) => setNameFmt(e.target.value)}>
                <option value="given-family">Given Family — Jun Liang Chan</option>
                <option value="family-given">Family Given — Chan Jun Liang</option>
              </select>
            </Field>
          </section>

          <section className="set-section">
            <h3>Instruction</h3>
            <Field label="Jargon" soon
              help={'Layman uses plain, beginner-friendly language ("put the flaps down"). Pilot uses standard aviation callouts ("select landing flap"). Visitor expects Layman; Trainee and Pilot expect Pilot — you\'ll be asked if they differ. Exam mode (below) expects Pilot.'}>
              <Segmented value={jargon} onChange={setJargon} options={[
                { value: "layman", label: "Layman" },
                { value: "pilot", label: "Pilot" },
              ]} />
            </Field>
            <Field label="Exam mode" soon
              help={<>When on, the next circuit is a silent assessment: no coaching, chat disabled, summary at the end. An <b>Exam</b> badge appears on the main menu and training screen.</>}>
              <Segmented value={exam} onChange={setExam} options={[
                { value: "off", label: "Off" },
                { value: "on", label: "On" },
              ]} />
            </Field>
          </section>
          <section className="set-section">
            <h3>MAVIS</h3>
            <nav className="set-links">
              <Link to="/scoring" className="set-link" onClick={onClose}>
                <ChecklistIcon />
                <span>Scoring Guide</span>
                <span className="set-link__chev" aria-hidden="true">›</span>
              </Link>
            </nav>
          </section>
          <section className="set-section">
            <h3>IRIS</h3>
            <nav className="set-links">
              <Link to="/upload" className="set-link" onClick={onClose}>
                <UploadIcon />
                <span>Upload</span>
                <span className="set-link__chev" aria-hidden="true">›</span>
              </Link>
              <Link to="/aoi-zones" className="set-link" onClick={onClose}>
                <TargetIcon />
                <span>AOI Zones</span>
                <span className="set-link__chev" aria-hidden="true">›</span>
              </Link>
            </nav>
          </section>
          {/* More sections go here. */}
        </div>
      </aside>
    </div>
  );
}
