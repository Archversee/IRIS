import { useSyncExternalStore } from "react";

// What the avatar's face should be doing right now. useInstructorVoice sets it
// when it starts playing a sentence and clears it when MAVIS stops talking (or
// is cut off). `video` is set when MAVIS sent the sentence with a pre-made
// lip-sync clip (MAVIS voice/lipsync.py): AvatarBox plays that clip following
// `audio` -- the element playing the sentence -- so the lips stay on the words.
// Without one, AvatarBox shows its general talking loop.
//   face = { video: url | null, audio: HTMLAudioElement, seq } | null
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
