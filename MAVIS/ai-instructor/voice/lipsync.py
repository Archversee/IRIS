"""Pre-made lip-sync clips for MAVIS's fixed lines.

Many sentences MAVIS says never change ("You have control.", the briefings,
"Check your speed."). Each of those can have a clip made ahead of time: the
sentence in MAVIS's own voice (a WAV) and the avatar saying it (an MP4, lips
matched to that WAV). When MAVIS says a sentence that has one, runtime/speech.py
plays that WAV instead of synthesising, and the tablet plays the MP4 in the
avatar box, locked to the audio (frontend AvatarBox / useInstructorVoice).
Sentences without a clip are spoken as before, over the idle loop.

The clips live in the shared media folder (config.yaml web.media_dir), under
avatar/lines/:
  lines.json     every candidate sentence (tools/avatar_lines.py collect)
  <key>.wav      the sentence in MAVIS's voice (tools/avatar_lines.py audio)
  <key>.mp4      the avatar saying it (tools/avatar_lines.py video)
  manifest.json  the sentences that have both, read here (tools/avatar_lines.py build)

Keyed by the exact sentence as runtime/speech.py speaks it: after
voice.units.standard_units and voice.tts.split_sentences.

Sentences spoken without a clip are counted in data/avatar_lines/heard.json, so
tools/avatar_lines.py collect also picks up the fixed lines that are only put
together at run time (a lesson step's cue, a lesson's own wording).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

LINES_DIR = "avatar/lines"          # under the media folder
MEDIA_URL = "/media/" + LINES_DIR   # how MAVIS serves it (web/server.py mounts /media)


def norm(text: str) -> str:
    return " ".join((text or "").split())


def key(text: str) -> str:
    """File name stem for a sentence: a short hash of its exact words."""
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:12]


def has_live_value(text: str) -> bool:
    """A sentence still holding a {placeholder} isn't fixed."""
    return bool(re.search(r"[{}]", text))


class LipsyncLibrary:
    """manifest.json, re-read when it changes (a clip made while MAVIS runs is
    used from the next time the sentence is said), plus the heard-lines counter."""

    def __init__(self, media_dir: Path, data_dir: Path):
        self.dir = Path(media_dir) / LINES_DIR
        self.manifest_path = self.dir / "manifest.json"
        self.heard_path = Path(data_dir) / "avatar_lines" / "heard.json"
        self._lines: dict[str, dict] = {}
        self._mtime = None
        self._checked = 0.0
        self._heard: dict[str, int] | None = None
        self._heard_dirty = False
        self._heard_saved = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        now = time.time()
        if now - self._checked < 2.0:
            return
        self._checked = now
        try:
            mtime = self.manifest_path.stat().st_mtime
        except OSError:
            self._lines, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self._lines = {k: v for k, v in (data.get("lines") or {}).items()
                           if (self.dir / v["wav"]).exists() and (self.dir / v["video"]).exists()}
            self._mtime = mtime
            log.info("lip-sync clips: %d sentences have one", len(self._lines))
        except Exception as e:
            log.warning("lip-sync manifest unreadable (%s): speaking without clips", e)
            self._lines, self._mtime = {}, mtime

    def lookup(self, sentence: str) -> dict | None:
        """{wav_url, video_url, dur, wav_path} for a sentence with a clip, else None."""
        self._refresh()
        e = self._lines.get(key(sentence))
        if not e or norm(e.get("text", "")) != norm(sentence):
            return None
        return {"wav_url": f"{MEDIA_URL}/{e['wav']}", "video_url": f"{MEDIA_URL}/{e['video']}",
                "dur": float(e.get("dur") or 0.0), "wav_path": self.dir / e["wav"]}

    def heard(self, sentence: str) -> None:
        """Count a sentence spoken without a clip (saved at most every 30 s)."""
        s = norm(sentence)
        if not s or has_live_value(s):
            return
        with self._lock:
            if self._heard is None:
                try:
                    self._heard = json.loads(self.heard_path.read_text(encoding="utf-8"))
                except Exception:
                    self._heard = {}
            self._heard[s] = self._heard.get(s, 0) + 1
            self._heard_dirty = True
            if time.time() - self._heard_saved >= 30.0:
                self._save_heard()

    def _save_heard(self) -> None:
        try:
            self.heard_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.heard_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._heard, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.heard_path)
            self._heard_dirty = False
            self._heard_saved = time.time()
        except Exception as e:
            log.debug("couldn't save heard lines: %s", e)

    def flush(self) -> None:
        with self._lock:
            if self._heard_dirty:
                self._save_heard()
