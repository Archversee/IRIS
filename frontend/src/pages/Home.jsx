import { useNavigate } from "react-router-dom";
import { RoleTag, useAuth } from "../auth/AuthContext.jsx";

// Full-screen landing after sign-in: one MARIE screen over splash-bg.jpeg
// (frontend/public/media, served by the frontend itself so it shows even
// when MAVIS isn't running). Clicking anywhere opens Preflight & Cockpit ->
// External Walk-Around (a client-side reading lesson, so nothing needs
// selecting on the MAVIS server first). The sidebar is hidden on this
// route (see App.jsx).
const WALK_AROUND = "/live?reading=lesson00_preflight";

// Full name with the acronym's letters (M·A·R·I·E) bolded and underlined.
// [word, highlight first letter?] -- "&" isn't part of MARIE.
const FULL_NAME = [
  ["Multimodal", true], ["Aviation", true], ["Review", true], ["&", false],
  ["Instruction", true], ["Engine", true],
];

export default function Home() {
  const nav = useNavigate();
  const { user, logout } = useAuth();

  return (
    <div className="landing">
      {/* Signed-in chip: name + role tag (-> Profile), and Log out. */}
      <div className="landing__user">
        <button type="button" className="landing__user-chip" onClick={() => nav("/profile")} title="Your profile">
          <span>{user.full_name || user.username}</span>
          <RoleTag role={user.role} small />
        </button>
        <button type="button" className="landing__logout" onClick={logout}>Log out</button>
      </div>

      <button type="button" className="landing__enter" onClick={() => nav(WALK_AROUND)}>
        <span className="landing__label">
          <span className="landing__title">MARIE</span>
          <span className="landing__full">
            {FULL_NAME.map(([word, hl], i) => (
              <span key={i}>
                {i > 0 && " "}
                {hl ? <><b>{word[0]}</b>{word.slice(1)}</> : word}
              </span>
            ))}
          </span>
          <span className="landing__cta">Click to enter</span>
        </span>
      </button>
    </div>
  );
}
