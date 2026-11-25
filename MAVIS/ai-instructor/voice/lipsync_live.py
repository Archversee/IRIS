"""Live lip-sync: MAVIS's link to the MuseTalk server (tools/musetalk_live.py).

With config avatar_lipsync.live on, MAVIS starts that server once at startup
-- with MuseTalk's own Python, whose packages clash with MAVIS's -- and then
sends it each sentence's WAV as it is said; back comes a silent MP4 of the
avatar saying it, which the tablet plays locked to the audio
(runtime/speech.py). Until the server is up (its models take ~15 s to load),
or if it isn't installed or fails, sentences go out without video and the
tablet shows its talking loop instead: lip-sync never holds MAVIS's voice up
for long, and never stops it.

A server already answering on the port (left from an earlier run, or started
by hand) is used as it is.
"""
from __future__ import annotations

import atexit
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("main")
START_TIMEOUT_S = 240          # models + avatar load; the first CUDA run compiles kernels


class LiveLipsync:
    def __init__(self, cfg: dict, here: Path):
        self.cfg = cfg
        self.here = Path(here)
        self.port = int(cfg.get("live_port") or 8790)
        self.base = f"http://127.0.0.1:{self.port}"
        self.max_wait_s = float(cfg.get("live_max_wait_s") or 4.0)
        self.ready = False
        self.proc: subprocess.Popen | None = None
        self._stopping = False

    # ---- server ------------------------------------------------------------
    def _health(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=1.5) as r:
                return bool(json.loads(r.read()).get("ok"))
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def start(self) -> None:
        """Start (or find) the server in the background; `ready` turns on when it answers."""
        threading.Thread(target=self._start, name="mavis-lipsync-live", daemon=True).start()

    def _start(self) -> None:
        if self._health():
            log.info("live lip-sync: using the MuseTalk server already on port %d", self.port)
            self.ready = True
            return
        python = Path(self.cfg.get("live_python") or "")
        musetalk = Path(self.cfg.get("live_musetalk") or "")
        if not python.is_file() or not musetalk.is_dir():
            log.warning("live lip-sync: avatar_lipsync.live_python / live_musetalk not found (%s, %s) -- "
                        "MAVIS talks over the talking loop instead", python, musetalk)
            return
        cmd = [str(python), "-I", str(self.here / "tools" / "musetalk_live.py"), "--musetalk", str(musetalk),
               "--port", str(self.port), "--decoder", str(self.cfg.get("live_decoder") or "taesd")]
        log.info("live lip-sync: starting MuseTalk (%s)", musetalk)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            log.warning("live lip-sync: couldn't start MuseTalk: %s", e)
            return
        atexit.register(self.stop)
        threading.Thread(target=self._pipe_log, name="mavis-lipsync-log", daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < START_TIMEOUT_S:
            if self.proc.poll() is not None:
                log.warning("live lip-sync: MuseTalk exited (code %s) -- talking loop instead",
                            self.proc.returncode)
                return
            if self._health():
                self.ready = True
                log.info("live lip-sync: ready after %.0f s", time.time() - t0)
                return
            time.sleep(1.0)
        log.warning("live lip-sync: MuseTalk didn't come up in %d s -- talking loop instead", START_TIMEOUT_S)

    def _pipe_log(self) -> None:
        """MuseTalk's own output, minus its progress noise, into MAVIS's log."""
        noise = ("UserWarning", "warnings.warn", "Redirects", "it/s]", "safetensors", "unsafe serialization")
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line and not any(n in line for n in noise):
                (log.warning if ("Error" in line or "Traceback" in line) else log.info)("musetalk: %s", line)
        if not self._stopping:
            self.ready = False

    def stop(self) -> None:
        self._stopping = True
        self.ready = False
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    # ---- one sentence ---------------------------------------------------------
    def render(self, wav: Path, out: Path) -> bool:
        """Blocking: the avatar saying `wav`, written to `out`. False if it failed."""
        if not self.ready:
            return False
        body = json.dumps({"wav": str(Path(wav).resolve()), "out": str(Path(out).resolve())}).encode()
        req = urllib.request.Request(f"{self.base}/render", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                res = json.loads(e.read())
            except ValueError:
                res = {"error": str(e)}
            log.warning("live lip-sync failed: %s", res.get("error"))
            return False
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("live lip-sync: no answer from MuseTalk: %s", e)
            if not self._health():
                self.ready = False
            return False
        return bool(res.get("ok")) and Path(out).exists()
