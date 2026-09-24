import { useEffect, useRef, useState } from "react";
import { mavisApi } from "../api/mavisApi.js";

// Text chat only -- push-to-talk voice input isn't ported yet (needs its
// own mic-recording hook, see docs/LIVE_REBUILD_PLAN.md, Slice G).
export default function ChatPanel({ transcript, chatEnabled }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [transcript?.length]);

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

  return (
    <div className="mavis-chat">
      <div className="mavis-chat__list" ref={listRef}>
        {(transcript || []).map((t, i) => (
          <p key={i} className={"mavis-chat__line mavis-chat__line--" + (t.who === "coach" ? "coach" : "you")}>
            <strong>{t.who === "coach" ? "MAVIS" : "You"}:</strong> {t.text}
          </p>
        ))}
      </div>
      <div className="mavis-chat__input">
        <input
          placeholder={chatEnabled === false ? "Chat disabled in this mode" : "Ask MAVIS something…"}
          value={text}
          disabled={chatEnabled === false}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button type="button" onClick={send} disabled={chatEnabled === false || sending || !text.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
