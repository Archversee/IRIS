import { useEffect, useRef, useState } from "react";
import { mediaUrl } from "../api/mavisApi.js";
import { useFace } from "../voice/faceSync.js";

// Idle-loop avatar video, muted (autoplay requires it), with a speaking
// glow driven by state.speaking. While MAVIS's voice is actually playing
// (voice/faceSync.js, set by useInstructorVoice), a talking loop plays over
// the idle loop instead -- TALK_VIDEO, muted: the sound is the voice's own
// audio -- and fades back to the idle loop when MAVIS stops talking.
// A sentence MAVIS sent with a pre-made lip-sync clip (MAVIS voice/lipsync.py,
// config avatar_lipsync.enabled) shows that clip instead, kept on the audio's
// position so the lips stay on the words however late either started.
// Falls back to a plain placeholder if no avatar video is configured on
// disk, rather than showing a browser's broken-video icon.
const TALK_VIDEO = "/media/avatar/intro.mp4";
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
  const talkRef = useRef(null);
  const faceRef = useRef(null);
  const [broken, setBroken] = useState(false);
  const [lipsync, setLipsync] = useState(false);   // a lip-sync clip is on screen
  const face = useFace();
  const clip = face?.video || null;
  const talking = !!face && !clip;                  // talking loop, no clip for this sentence

  useEffect(() => {
    videoRef.current?.play().catch(() => {}); // autoplay can be blocked until a user gesture
  }, []);

  // the talking loop: carries on from where it was, so sentences don't all start alike
  useEffect(() => {
    const t = talkRef.current;
    if (!t) return;
    if (talking) t.play().catch(() => {});
    else t.pause();
  }, [talking]);

  // a sentence's own lip-sync clip
  useEffect(() => {
    const v = faceRef.current;
    if (!v) return;
    if (!clip) {
      const idle = videoRef.current;
      if (lipsyncShown(v) && idle) {
        idle.currentTime = idleTimeAt(v.currentTime);   // same pose under the clip as it fades out
      }
      setLipsync(false);
      v.pause();
      return;
    }
    const a = face.audio;
    let raf = 0;
    v.src = clip;
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
    const shown = () => setLipsync(true);
    const failed = () => setLipsync(false);           // no clip after all: just the idle loop
    v.addEventListener("playing", shown);
    v.addEventListener("error", failed);
    raf = requestAnimationFrame(follow);
    return () => {
      cancelAnimationFrame(raf);
      v.removeEventListener("playing", shown);
      v.removeEventListener("error", failed);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip, face?.seq]);

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
            ref={talkRef}
            className={"mavis-avatar__face" + (talking ? " is-on" : "")}
            src={mediaUrl(TALK_VIDEO)}
            loop
            muted
            playsInline
            preload="auto"
            aria-hidden="true"
          />
          <video
            ref={faceRef}
            className={"mavis-avatar__face" + (lipsync && clip ? " is-on" : "")}
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

// a lip-sync clip had been playing in this element
function lipsyncShown(v) {
  return !!v.getAttribute("src") && v.readyState >= 2 && v.currentTime > 0;
}
