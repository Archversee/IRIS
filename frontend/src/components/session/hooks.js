import { useEffect, useRef, useState } from "react";
import { VIDEO_SYNC_TOLERANCE, VIDEO_SYNC_TOLERANCE_PLAYING } from "./constants.js";

export function useSyncedVideo(ref, src, offsetSec, curT, playing, speed) {
  useEffect(() => {
    const video = ref.current;
    if (!video || !src || Number.isNaN(video.duration)) return;
    const target = Math.max(0, curT + offsetSec);
    const tolerance = playing ? VIDEO_SYNC_TOLERANCE_PLAYING : VIDEO_SYNC_TOLERANCE;
    if (Math.abs(video.currentTime - target) > tolerance) {
      video.currentTime = target;
    }
  }, [curT, src, playing]);

  useEffect(() => {
    const video = ref.current;
    if (!video || !src) return;
    if (playing) video.play().catch(() => {});
    else video.pause();
  }, [playing, src]);

  useEffect(() => {
    const video = ref.current;
    if (video) video.playbackRate = speed;
  }, [speed, src]);
}

export function useGazeStretch(gaze, curT) {
  const prevRef = useRef(null);
  const [stretch, setStretch] = useState({ angleDeg: 0, factor: 1 });

  useEffect(() => {
    if (!gaze || gaze.x == null) return;
    const prev = prevRef.current;
    prevRef.current = { x: gaze.x, y: gaze.y, t: curT };
    if (!prev) return;
    const dt = curT - prev.t;
    if (dt <= 0) return;
    const dx = gaze.x - prev.x, dy = gaze.y - prev.y;
    const dist = Math.hypot(dx, dy);
    const speed = dist / dt;
    const factor = 1 + Math.min(1.8, speed / 4000);
    const angleDeg = dist > 0.5 ? Math.atan2(dy, dx) * (180 / Math.PI) : 0;
    setStretch({ angleDeg, factor });
  }, [gaze?.x, gaze?.y, curT]);

  return stretch;
}
