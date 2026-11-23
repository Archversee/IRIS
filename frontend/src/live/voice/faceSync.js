import { useSyncExternalStore } from "react";

// Which lip-sync clip the avatar should be showing right now, if any.
// useInstructorVoice sets it when it starts a sentence that MAVIS sent with a
// `video` (a pre-made clip of the avatar saying that fixed sentence: MAVIS
// voice/lipsync.py), and clears it when the sentence ends or MAVIS is cut off.
// AvatarBox plays the clip over the idle loop, following `audio` -- the
// element playing the sentence -- so the lips stay on the words.
//   face = { video: url, audio: HTMLAudioElement, seq } | null
let face = null;
const listeners = new Set();

export function setFace(next) {
  if (face === next) return;
  face = next;
  for (const l of listeners) l();
}

function subscribe(l) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useFace() {
  return useSyncExternalStore(subscribe, () => face);
}
