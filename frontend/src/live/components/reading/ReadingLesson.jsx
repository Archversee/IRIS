import { useRef, useState } from "react";
import CockpitViewer from "./CockpitViewer.jsx";
import ChecklistPanel from "./ChecklistPanel.jsx";
import { mavisApi } from "../../api/mavisApi.js";
import { readingCfg } from "./readingConfig.js";

// Preflight/cockpit "reading" lessons -- no telemetry, a narrated
// checklist with an optional 3D walk-around/cockpit viewer. Mirrors the
// original app's openReadingLesson()/pfStep()/playPreflightAudio() flow.
export default function ReadingLesson({ lessonId, data }) {
  const [tab, setTab] = useState("3d");
  const [selectedId, setSelectedId] = useState(null);
  const [playingId, setPlayingId] = useState(null);
  const [playAll, setPlayAll] = useState(false);
  const audioRef = useRef(null);
  const cfg = readingCfg(lessonId);
  const checkpoints = data?.checkpoints || [];

  async function playCheckpoint(id) {
    setPlayingId(id);
    try {
      const res = await mavisApi.readingSay(id, lessonId);
      if (res?.url && audioRef.current) {
        audioRef.current.src = "/mavis" + res.url;
        await audioRef.current.play();
      }
    } catch {
      setPlayingId(null);
    }
  }

  function onAudioEnded() {
    setPlayingId(null);
    if (playAll) {
      const idx = checkpoints.findIndex((c) => c.id === selectedId);
      const next = checkpoints[idx + 1];
      if (next) {
        setSelectedId(next.id);
        playCheckpoint(next.id);
      } else {
        setPlayAll(false);
      }
    }
  }

  function select(id) {
    setSelectedId(id);
  }

  function step(delta) {
    const idx = checkpoints.findIndex((c) => c.id === selectedId);
    const nextIdx = Math.max(0, Math.min(checkpoints.length - 1, idx < 0 ? 0 : idx + delta));
    setSelectedId(checkpoints[nextIdx]?.id);
  }

  function togglePlayAll() {
    if (playAll) {
      setPlayAll(false);
      audioRef.current?.pause();
      return;
    }
    setPlayAll(true);
    const start = selectedId || checkpoints[0]?.id;
    if (start) {
      setSelectedId(start);
      playCheckpoint(start);
    }
  }

  const idx = checkpoints.findIndex((c) => c.id === selectedId);

  return (
    <div>
      <div className="mavis-topbar">
        <div>
          <h2 style={{ margin: 0 }}>{data?.title}</h2>
          <p className="muted" style={{ margin: 0 }}>{cfg.blurb}</p>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 10, alignItems: "center" }}>
        <button type="button" className={tab === "3d" ? "" : "secondary"} onClick={() => setTab("3d")}>
          {cfg.camera === "fixed" ? "3D cockpit" : "3D walk-around"}
        </button>
        <button type="button" className={tab === "list" ? "" : "secondary"} onClick={() => setTab("list")}>
          Checklist
        </button>
        <span className="spacer" />
        <button type="button" className="secondary" onClick={() => step(-1)} disabled={idx <= 0}>‹ Prev</button>
        <span className="muted">{checkpoints.length ? (idx < 0 ? `/ ${checkpoints.length}` : `${idx + 1} / ${checkpoints.length}`) : ""}</span>
        <button type="button" className="secondary" onClick={() => step(1)} disabled={!checkpoints.length || idx >= checkpoints.length - 1}>Next ›</button>
        <button type="button" onClick={togglePlayAll}>{playAll ? "⏸ Stop" : "▶ Play all"}</button>
      </div>

      {tab === "3d" ? (
        <>
          <CockpitViewer lessonId={lessonId} checkpoints={checkpoints} selectedId={selectedId} onPick={select} />
          <p className="muted" style={{ marginTop: 8 }}>{cfg.hint}</p>
          {selectedId ? (
            (() => {
              const cp = checkpoints.find((c) => c.id === selectedId);
              if (!cp) return null;
              return (
                <div className="card" style={{ marginTop: 10 }}>
                  <div className="mavis-pf-item__head">
                    <h4 style={{ margin: 0 }}>{cp.title}</h4>
                    <button type="button" className="secondary" onClick={() => playCheckpoint(cp.id)}>
                      {playingId === cp.id ? "🔊 Playing…" : "🔊 Play"}
                    </button>
                  </div>
                  <p className="muted">{cp.say}</p>
                  {cp.details?.length > 0 && <ul>{cp.details.map((d, j) => <li key={j}>{d}</li>)}</ul>}
                </div>
              );
            })()
          ) : (
            <p className="muted card" style={{ marginTop: 10 }}>Click a highlighted point on the aircraft to bring up its checkpoint.</p>
          )}
        </>
      ) : (
        <ChecklistPanel
          checkpoints={checkpoints}
          selectedId={selectedId}
          playingId={playingId}
          onSelect={select}
          onPlay={playCheckpoint}
        />
      )}

      <audio ref={audioRef} onEnded={onAudioEnded} hidden />
    </div>
  );
}
