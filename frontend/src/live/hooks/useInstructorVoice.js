import { useEffect, useRef } from "react";

// MAVIS doesn't play its voice itself when voice.output is "tablet" (the
// default in config.yaml): it synthesises each line, a sentence at a time, to
// WAVs and publishes state.audio = {gen, clips: [{seq, url}, ...]} (backend:
// MAVIS runtime/speech.py). The browser plays every clip it hasn't played yet,
// in order and back to back -- a clip that arrives while another is playing
// waits for it instead of cutting it off. gen changes only when MAVIS is cut
// off (a more urgent callout, stop): then playback stops and the queue is
// dropped. URLs are MAVIS-origin paths, re-based through the /mavis proxy.
const SILENT_WAV = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAA==";
const MAX_QUEUE = 10;     // far behind (tablet asleep, network stall): skip to the newest

// state.audio from an older MAVIS ({url, seq} only) as one clip
const clipsOf = (audio) => audio?.clips || (audio?.url ? [{ seq: audio.seq, url: audio.url }] : []);

export function useInstructorVoice(audio, active) {
  const voiceRef = useRef(null);
  const lastSeqRef = useRef(null);     // newest clip queued or played
  const genRef = useRef(null);
  const queueRef = useRef([]);         // urls waiting their turn
  const playingRef = useRef(false);
  const nextRef = useRef(() => {});    // start the next queued clip

  if (!voiceRef.current && typeof Audio !== "undefined") voiceRef.current = new Audio();

  // Browsers block play() until the page has had a user gesture; prime the
  // element on any tap/click so later lines (which arrive without one) play.
  useEffect(() => {
    const voice = voiceRef.current;
    if (!voice) return;
    const unlock = () => {
      if (!voice.paused || playingRef.current) return;
      voice.src = SILENT_WAV;
      voice.play().then(() => voice.pause()).catch(() => {});
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  // one clip after the other
  useEffect(() => {
    const voice = voiceRef.current;
    if (!voice) return;
    const next = () => {
      const url = queueRef.current.shift();
      if (!url) { playingRef.current = false; return; }
      playingRef.current = true;
      voice.src = "/mavis" + url;
      voice.play().catch((e) => {
        console.warn("MAVIS voice blocked by the browser:", e);
        playingRef.current = false;
        queueRef.current = [];
      });
    };
    nextRef.current = next;
    const onEnd = () => playingRef.current && next();
    voice.addEventListener("ended", onEnd);
    voice.addEventListener("error", onEnd);          // a clip that won't load: carry on with the rest
    return () => { voice.removeEventListener("ended", onEnd); voice.removeEventListener("error", onEnd); };
  }, []);

  const stop = () => {
    queueRef.current = [];
    playingRef.current = false;
    voiceRef.current?.pause();
  };

  // Entering a lesson: whatever is already published is from before, so only
  // newer clips get played. Leaving: stop.
  useEffect(() => {
    if (active) {
      const clips = clipsOf(audio);
      lastSeqRef.current = clips.length ? clips[clips.length - 1].seq : null;
      genRef.current = audio?.gen ?? null;
    } else stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => {
    const voice = voiceRef.current;
    if (!active || !voice || !audio) return;
    if (audio.gen != null && audio.gen !== genRef.current) {
      if (genRef.current != null) stop();           // MAVIS was cut off: drop the rest of that line
      if (genRef.current != null && audio.gen < genRef.current) lastSeqRef.current = null;   // MAVIS restarted
      genRef.current = audio.gen;
    }
    const fresh = clipsOf(audio).filter((c) => lastSeqRef.current == null || c.seq > lastSeqRef.current);
    if (!fresh.length) return;
    lastSeqRef.current = fresh[fresh.length - 1].seq;
    queueRef.current.push(...fresh.map((c) => c.url));
    if (queueRef.current.length > MAX_QUEUE) queueRef.current = queueRef.current.slice(-MAX_QUEUE);
    if (!playingRef.current) nextRef.current();
  }, [active, audio]);

  // Stop talking when leaving the page.
  useEffect(() => () => voiceRef.current?.pause(), []);
}
