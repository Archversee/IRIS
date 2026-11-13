import { useEffect, useRef } from "react";
import { VIDEO_SYNC_TOLERANCE, VIDEO_SYNC_TOLERANCE_PLAYING } from "./constants.js";

export function useSyncedVideo(ref, src, offsetSec, curT, playing, speed) {
  // Dragging across the timeline updates curT on every mousemove -- many
  // times a second. Re-issuing video.currentTime before the browser's
  // decoder finishes the PREVIOUS seek is a known way to leave an HTML5
  // <video> stuck frozen on one frame, especially for a long-GOP MP4 like
  // an OBS recording. latestTargetRef always holds where we actually want
  // to be; the "seeked" listener below re-checks against it once the
  // decoder is free, so the video still lands on the right frame once
  // dragging settles, even though most in-between seeks get skipped.
  const latestTargetRef = useRef(0);

  useEffect(() => {
    const video = ref.current;
    if (!video || !src) return;
    const onSeeked = () => {
      const tolerance = playing ? VIDEO_SYNC_TOLERANCE_PLAYING : VIDEO_SYNC_TOLERANCE;
      if (Math.abs(video.currentTime - latestTargetRef.current) > tolerance) {
        video.currentTime = latestTargetRef.current;
      }
    };
    video.addEventListener("seeked", onSeeked);
    return () => video.removeEventListener("seeked", onSeeked);
  }, [src, playing]);

  useEffect(() => {
    const video = ref.current;
    if (!video || !src || Number.isNaN(video.duration)) return;
    const target = Math.max(0, curT + offsetSec);
    latestTargetRef.current = target;
    if (video.seeking) return; // let the in-flight seek finish -- the "seeked" listener above will catch up to `target` once it does
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
