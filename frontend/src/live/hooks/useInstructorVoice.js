import { useEffect, useRef } from "react";
import { setFace } from "../voice/faceSync.js";

// MAVIS doesn't play its voice itself when voice.output is "tablet" (the
// default in config.yaml): it synthesises each line, a sentence at a time, to
// WAVs and publishes state.audio = {gen, clips: [{seq, url}, ...]} (backend:
// MAVIS runtime/speech.py). The browser plays every clip it hasn't played yet,
// in order and back to back -- a clip that arrives while another is playing
// waits for it instead of cutting it off. clips lists only what MAVIS is
// saying now. gen changes only when MAVIS is cut off (a more urgent callout,
// a new session, stop): then playback stops and the queue is dropped. URLs are MAVIS-origin paths, re-based through the /mavis proxy.
// While a sentence plays, the avatar shows its talking loop (voice/faceSync.js);
// a fixed sentence with a pre-made lip-sync clip comes with `video`, and then
// the avatar shows that clip in step with this audio instead.
const SILENT_WAV = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAA==";
const MAX_QUEUE = 10;     // far behind (tablet asleep, network stall): skip to the newest

// state.audio from an older MAVIS ({url, seq} only) as one clip
const clipsOf = (audio) => audio?.clips || (audio?.url ? [{ seq: audio.seq, url: audio.url }] : []);

export function useInstructorVoice(audio, active) {
  const voiceRef = useRef(null);
  const lastSeqRef = useRef(null);     // newest clip queued or played
  const genRef = useRef(null);
  const queueRef = useRef([]);         // clips ({url, video?, seq}) waiting their turn
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
      const clip = queueRef.current.shift();
      if (!clip) { playingRef.current = false; setFace(null); return; }
      playingRef.current = true;
      voice.src = "/mavis" + clip.url;
      // every sentence: the avatar talks while it plays (its own lip-sync clip if it has one)
      setFace({ video: clip.video ? "/mavis" + clip.video : null, audio: voice, seq: clip.seq });
      voice.play().catch((e) => {
        console.warn("MAVIS voice blocked by the browser:", e);
        playingRef.current = false;
        queueRef.current = [];
        setFace(null);
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
    setFace(null);
  };

  // Starting to listen (lesson started, scorecard closed): MAVIS lists only
  // what it is saying right now, so play all of it -- the first lines of a
  // lesson are often already listed by the time the scorecard has cleared.
  // (An older MAVIS keeps its last line listed forever: skip that one.) Leaving: stop.
  useEffect(() => {
    if (active) {
      const clips = clipsOf(audio);
      lastSeqRef.current = audio?.gen == null && clips.length ? clips[clips.length - 1].seq : null;
      genRef.current = audio?.gen ?? null;
    } else stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // An older MAVIS ({url, seq}, no gen) withdraws a line by clearing url -- stop
  // it here too. A current MAVIS also empties its clip list (so url goes away)
  // once a line has *finished* by its own clock; that must not stop anything:
  // a tablet fetches each clip over Wi-Fi only when the one before ends, so it
  // runs behind the server and would be cut off mid-line -- and a bare pause()
  // never fires "ended", so playingRef stayed true and every later line just
  // queued, silent. A real withdrawal (stop_speech) arrives as a new gen below.
  // So only an older MAVIS (no gen) is stopped here. (This used to run for every
  // MAVIS: on a tablet, a long line was cut off part way and the voice stayed
  // silent from then on while the chat carried on.)
  const legacy = audio != null && audio.gen == null;
  useEffect(() => {
    if (legacy && !audio?.url) stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audio?.url, legacy]);

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
    queueRef.current.push(...fresh);
    if (queueRef.current.length > MAX_QUEUE) queueRef.current = queueRef.current.slice(-MAX_QUEUE);
    // "playing" but paused and not finished: something outside paused it (the tablet
    // interrupted its audio, say) and "ended" will never come -- carry on with the queue
    // (play() unpauses at once, so a clip that is just starting never looks like this)
    if (playingRef.current && voice.paused && !voice.ended) playingRef.current = false;
    if (!playingRef.current) nextRef.current();
  }, [active, audio]);

  // Stop talking when leaving the page.
  useEffect(() => () => { voiceRef.current?.pause(); setFace(null); }, []);
}
