import { useEffect, useRef, useState } from "react";
import { mavisApi } from "../api/mavisApi.js";

// MAVIS tags transcript lines who: "instructor" (its own callouts/replies)
// or who: "trainee" (what the pilot typed/said) -- see coach/llm_coach.py's
// say() and main.py's chat handler. Anything that isn't the trainee is MAVIS.
const isTrainee = (t) => t.who === "trainee";

function timeOf(t) {
  if (!t?.t) return "";
  return new Date(t.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Text chat only -- push-to-talk voice input isn't ported yet (needs its
// own mic-recording hook, see docs/LIVE_REBUILD_PLAN.md, Slice G).
export default function ChatPanel({ transcript, chatEnabled }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);
  const lines = transcript || [];

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [lines.length]);

  async function send() {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    setText("");
    try {
      await mavisApi.chat(t);
    } catch {
      // best-effort -- the transcript itself is server-driven, nothing to roll back locally
    } finally {
      setSending(false);
    }
  }

  const disabled = chatEnabled === false;

  return (
    <div className="mavis-chat">
      <div className="mavis-chat__head">
        <span className="mavis-chat__title">Instructor chat</span>
        <span className="mavis-chat__count">{lines.length ? `${lines.length} messages` : ""}</span>
      </div>

      <div className="mavis-chat__list" ref={listRef}>
        {lines.length === 0 && (
          <div className="mavis-chat__empty">MAVIS's callouts and your questions will appear here.</div>
        )}
        {lines.map((t, i) => {
          const you = isTrainee(t);
          // Consecutive lines from the same sender are grouped under one name.
          const first = i === 0 || isTrainee(lines[i - 1]) !== you;
          return (
            <div key={i} className={"mavis-msg " + (you ? "mavis-msg--you" : "mavis-msg--mavis") + (first ? " is-first" : "")}>
              {first && (
                <div className="mavis-msg__meta">
                  {!you && <span className="mavis-msg__avatar">M</span>}
                  <span className="mavis-msg__who">{you ? "You" : "MAVIS"}</span>
                  <span className="mavis-msg__time">{timeOf(t)}</span>
                </div>
              )}
              <div className="mavis-msg__bubble">{t.text}</div>
            </div>
          );
        })}
      </div>

      <div className="mavis-chat__input">
        <input
          placeholder={disabled ? "Chat disabled in this mode" : "Ask MAVIS something…"}
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button
          type="button"
          className="mavis-chat__send"
          title="Send"
          aria-label="Send"
          onClick={send}
          disabled={disabled || sending || !text.trim()}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  );
}
