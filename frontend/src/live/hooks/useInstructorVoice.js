import { useEffect, useRef } from "react";

// MAVIS doesn't play its voice itself when voice.output is "tablet" (the
// default in config.yaml): it synthesises each line to a WAV and publishes
// state.audio = {url: "/tts/say_N.wav", seq: N}. The browser plays each new
// seq -- the same thing the original web/static/index.html did in
// playInstructorVoice(). URLs are MAVIS-origin paths, so they're re-based
// through the /mavis proxy like every other MAVIS request.
const SILENT_WAV = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAA==";

export function useInstructorVoice(audio, active) {
  const voiceRef = useRef(null);
  const lastSeqRef = useRef(null);

  if (!voiceRef.current && typeof Audio !== "undefined") voiceRef.current = new Audio();

  // Browsers block play() until the page has had a user gesture; prime the
  // element on any tap/click so later lines (which arrive without one) play.
  useEffect(() => {
    const voice = voiceRef.current;
    if (!voice) return;
    const unlock = () => {
      if (!voice.paused) return;
      voice.src = SILENT_WAV;
      voice.play().then(() => voice.pause()).catch(() => {});
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  // Entering a lesson: whatever line is already published is from before,
  // so only lines with a newer seq get played.
  useEffect(() => {
    if (active) lastSeqRef.current = audio?.seq ?? null;
    else voiceRef.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // MAVIS withdrew the line it was saying (stop_speech: the lesson moved on,
  // the briefing was cut short, a restart) -- stop it here too.
  useEffect(() => {
    if (!audio?.url) voiceRef.current?.pause();
  }, [audio?.url]);

  useEffect(() => {
    const voice = voiceRef.current;
    if (!active || !voice || !audio?.url || audio.seq === lastSeqRef.current) return;
    lastSeqRef.current = audio.seq;
    voice.src = "/mavis" + audio.url;
    voice.play().catch((e) => console.warn("MAVIS voice blocked by the browser:", e));
  }, [active, audio?.seq, audio?.url]);

  // Stop talking when leaving the page.
  useEffect(() => () => voiceRef.current?.pause(), []);
}
