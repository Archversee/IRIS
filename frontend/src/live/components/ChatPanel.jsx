import { useEffect, useRef, useState } from "react";
import { mavisApi } from "../api/mavisApi.js";
import { recordMicWav, VOICE_REASONS } from "../voice/recordWav.js";

// MAVIS tags transcript lines who: "instructor" (its own callouts/replies)
// or who: "trainee" (what the pilot typed/said) -- see coach/llm_coach.py's
// say() and main.py's chat handler. Anything that isn't the trainee is MAVIS.
const isTrainee = (t) => t.who === "trainee";

function timeOf(t) {
  if (!t?.t) return "";
  return new Date(t.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Typed chat plus push-to-talk: hold the mic button (or the joystick's PTT
// button, joyPtt = state.ctrl.ptt) and speak -- MAVIS transcribes it and
// handles it exactly like typed text ("make it night", "can we get some rain").
export default function ChatPanel({ transcript, chatEnabled, joyPtt = false, speakerName = "" }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [talking, setTalking] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("");
  const stopRef = useRef(null);
  const busyRef = useRef(false);
  const statusTimer = useRef(null);
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

  // status line: "Listening…"/"Transcribing…" stay up; results fade after a few seconds
  function showStatus(msg, sticky = false) {
    clearTimeout(statusTimer.current);
    setVoiceStatus(msg);
    if (!sticky && msg) statusTimer.current = setTimeout(() => setVoiceStatus(""), 6000);
  }

  // leaving the page mid-recording must release the mic
  useEffect(() => () => {
    stopRef.current?.();
    clearTimeout(statusTimer.current);
  }, []);

  async function beginTalk() {
    if (disabled || busyRef.current) return;
    busyRef.current = true;
    const stop = new Promise((r) => { stopRef.current = r; });
    setTalking(true);
    showStatus("Opening microphone…", true);
    let rec;
    try {
      rec = await recordMicWav({
        stopSignal: stop,
        onReady: () => showStatus("Speak now… release when done", true),
      });
    } catch (e) {
      stopRef.current = null;
      busyRef.current = false;
      setTalking(false);
      showStatus(VOICE_REASONS[e.code] || VOICE_REASONS.mic_error);
      return;
    }
    setTalking(false);
    if (rec.durationMs < 350 || rec.peak < 0.002) {
      busyRef.current = false;
      const heard = `${(rec.durationMs / 1000).toFixed(1)} s, level ${rec.peak.toFixed(3)}, audio ${rec.engine}`;
      showStatus(`${rec.durationMs < 350 ? VOICE_REASONS.too_short : VOICE_REASONS.low_level} (${heard})`);
      return;
    }
    showStatus("Transcribing…", true);
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 45000);
    try {
      const j = await mavisApi.voicePtt(rec.blob, speakerName, ac.signal);
      showStatus(j?.ok ? `Heard: “${j.text}”` : (VOICE_REASONS[j?.reason] || "Couldn't use that recording."));
    } catch (e) {
      showStatus(e?.name === "AbortError" ? VOICE_REASONS.stt_timeout : "Couldn't reach speech recognition.");
    } finally {
      clearTimeout(timer);
      busyRef.current = false;
    }
  }

  function endTalk() {
    if (stopRef.current) {
      stopRef.current();
      stopRef.current = null;
    }
    setTalking(false);
  }

  // joystick push-to-talk mirrors holding the on-screen button
  useEffect(() => {
    if (joyPtt) beginTalk();
    else endTalk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [joyPtt]);

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
          className={"mavis-chat__mic" + (talking ? " is-hot" : "")}
          title="Hold to talk (or hold the joystick push-to-talk button)"
          aria-label="Hold to talk"
          disabled={disabled}
          onPointerDown={(e) => {
            e.preventDefault();
            // keep the hold if the finger drifts off the button while talking
            try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine */ }
            beginTalk();
          }}
          onPointerUp={endTalk}
          onPointerCancel={endTalk}
          onLostPointerCapture={endTalk}
          onContextMenu={(e) => e.preventDefault()}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><line x1="12" y1="17" x2="12" y2="22" />
          </svg>
        </button>
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
      {voiceStatus && <div className="mavis-chat__voice" role="status">{voiceStatus}</div>}
    </div>
  );
}
