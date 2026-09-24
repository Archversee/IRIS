import { useEffect, useRef, useState } from "react";
import { mediaUrl } from "../api/mavisApi.js";

// Idle-loop avatar video, muted (autoplay requires it), with a speaking
// glow driven by state.speaking. Intro clips / unmuted playback aren't
// ported yet -- see docs/LIVE_REBUILD_PLAN.md, this is the idle case only.
// Falls back to a plain placeholder if no avatar video is configured on
// disk, rather than showing a browser's broken-video icon.
export default function AvatarBox({ speaking }) {
  const videoRef = useRef(null);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    videoRef.current?.play().catch(() => {}); // autoplay can be blocked until a user gesture
  }, []);

  return (
    <div className={"mavis-avatar" + (speaking ? " is-speaking" : "")}>
      {broken ? (
        <div className="mavis-avatar__placeholder">MAVIS</div>
      ) : (
        <video
          ref={videoRef}
          src={mediaUrl("/media/avatar/idle.mp4")}
          loop
          muted
          playsInline
          onError={() => setBroken(true)}
        />
      )}
    </div>
  );
}
