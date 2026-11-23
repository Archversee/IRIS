import { useEffect, useRef, useState } from "react";
import { mediaUrl } from "../api/mavisApi.js";
import { useFace } from "../voice/faceSync.js";

// Idle-loop avatar video, muted (autoplay requires it), with a speaking
// glow driven by state.speaking. When MAVIS says a fixed sentence that has a
// pre-made lip-sync clip (MAVIS voice/lipsync.py), that clip plays over the
// idle loop, muted -- the sound is the voice's own audio -- and is kept on the
// audio's position, so the lips stay on the words however late either started.
// Back to the idle loop the moment the sentence ends. Intro clips / unmuted
// playback aren't ported yet -- see docs/LIVE_REBUILD_PLAN.md.
// Falls back to a plain placeholder if no avatar video is configured on
// disk, rather than showing a browser's broken-video icon.
const SEEK_IF_OFF_S = 0.25;    // further apart than this: jump the video to the audio
const NUDGE_RATE = 0.5;        // closer: speed the video up / slow it down to catch up
// The clips are drawn on the first CLIP_SOURCE_S of idle.mp4, played forward then
// backward over and over (MAVIS tools/musetalk_batch.py --source-seconds, at
// CLIP_FPS). Where a clip stops, the idle loop picks up on the same frame.
const CLIP_SOURCE_S = 8;
const CLIP_FPS = 25;

// idle.mp4's time showing the same frame as `t` seconds into a clip
function idleTimeAt(t) {
  const n = Math.round(CLIP_SOURCE_S * CLIP_FPS);
  const i = Math.floor(t * CLIP_FPS) % (2 * n);
  return (i < n ? i : 2 * n - 1 - i) / CLIP_FPS;
}

export default function AvatarBox({ speaking }) {
  const videoRef = useRef(null);
  const faceRef = useRef(null);
  const [broken, setBroken] = useState(false);
  const [talking, setTalking] = useState(false);   // the lip-sync clip is on screen
  const face = useFace();

  useEffect(() => {
    videoRef.current?.play().catch(() => {}); // autoplay can be blocked until a user gesture
  }, []);

  useEffect(() => {
    const v = faceRef.current;
    if (!v) return;
    if (!face) {
      const idle = videoRef.current;
      if (idle && v.readyState >= 2 && v.currentTime > 0) {
        idle.currentTime = idleTimeAt(v.currentTime);   // same pose under the clip as it fades out
      }
      setTalking(false);
      v.pause();
      return;
    }
    const a = face.audio;
    let raf = 0;
    v.src = face.video;
    v.currentTime = 0;
    v.playbackRate = 1;
    const follow = () => {
      raf = requestAnimationFrame(follow);
      if (v.readyState < 2) return;                    // not loaded yet: idle loop stays on
      if (a.paused || a.ended) { if (!v.paused) v.pause(); return; }
      const off = a.currentTime - v.currentTime;
      if (Math.abs(off) > SEEK_IF_OFF_S || v.ended) v.currentTime = Math.min(a.currentTime, v.duration || a.currentTime);
      else v.playbackRate = Math.min(1.1, Math.max(0.9, 1 + off * NUDGE_RATE));
      if (v.paused && !v.ended) v.play().catch(() => {});
    };
    const shown = () => setTalking(true);
    const failed = () => setTalking(false);           // no clip after all: just the idle loop
    v.addEventListener("playing", shown);
    v.addEventListener("error", failed);
    raf = requestAnimationFrame(follow);
    return () => {
      cancelAnimationFrame(raf);
      v.removeEventListener("playing", shown);
      v.removeEventListener("error", failed);
    };
  }, [face]);

  return (
    <div className={"mavis-avatar" + (speaking ? " is-speaking" : "")}>
      {broken ? (
        <div className="mavis-avatar__placeholder">MAVIS</div>
      ) : (
        <>
          <video
            ref={videoRef}
            src={mediaUrl("/media/avatar/idle.mp4")}
            loop
            muted
            playsInline
            onError={() => setBroken(true)}
          />
          <video
            ref={faceRef}
            className={"mavis-avatar__face" + (talking ? " is-on" : "")}
            muted
            playsInline
            preload="auto"
            aria-hidden="true"
          />
        </>
      )}
    </div>
  );
}
