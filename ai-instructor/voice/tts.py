"""Local TTS behind one interface. engine: auto tries kokoro -> pyttsx3 -> console.
All engines run fully offline. speak() is blocking; call via executor."""
from __future__ import annotations
import logging
import re
import subprocess
import tempfile

log = logging.getLogger(__name__)

# Aviation speed symbols, spoken as letter names, not one syllable.
_AVIATION_TTS = (
    (re.compile(r"\bVr\b"), "V R"),
    (re.compile(r"\bVy\b"), "V Y"),
    (re.compile(r"\bVne\b", re.I), "V N E"),
    (re.compile(r"\bVfe\b", re.I), "V F E"),
)


def prepare_for_speech(text: str) -> str:
    out = text
    for pat, repl in _AVIATION_TTS:
        out = pat.sub(repl, out)
    return out


class TTS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.engine = None
        want = cfg.get("engine", "auto")
        primary = [] if want in ("auto", None) else [want]
        order = primary + [x for x in ("kokoro", "piper", "pyttsx3") if x not in primary]
        for name in order:
            if name == "none":
                break
            try:
                getattr(self, f"_init_{name}")()
                self.engine = name
                break
            except Exception as e:
                log.info("TTS %s unavailable: %s", name, e)
        log.info("TTS engine: %s", self.engine or "console only")

    # ---- kokoro (preferred: near-human, local, GPU/CPU) ----
    def _init_kokoro(self):
        from kokoro import KPipeline           # pip install kokoro soundfile
        self._kokoro = KPipeline(lang_code="a")
        self._voice = self.cfg.get("kokoro_voice", "af_heart")

    def _speak_kokoro(self, text: str):
        import numpy as np
        import sounddevice as sd
        chunks = [audio for _, _, audio in self._kokoro(text, voice=self._voice)]
        sd.play(np.concatenate(chunks), 24000, blocking=True)

    # ---- piper (very fast, lighter voices) ----
    def _init_piper(self):
        model = self.cfg.get("piper_model")
        if not model:
            raise RuntimeError("no piper_model configured")
        subprocess.run(["piper", "--help"], capture_output=True, check=True)
        self._piper_model = model

    def _speak_piper(self, text: str):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            subprocess.run(["piper", "--model", self._piper_model, "--output_file", f.name],
                           input=text.encode(), check=True, capture_output=True)
            self._play_wav(f.name)

    # ---- pyttsx3 (Windows SAPI, always available on the sim PC) ----
    def _init_pyttsx3(self):
        import pyttsx3
        self._px = pyttsx3.init()
        self._px.setProperty("rate", self.cfg.get("rate", 175))
        # Prefer a female SAPI voice so fallback still matches MAVIS
        want = (self.cfg.get("sapi_voice") or "zira|female|woman").lower()
        keys = [k.strip() for k in want.split("|") if k.strip()]
        try:
            for v in self._px.getProperty("voices") or []:
                blob = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')}".lower()
                if any(k in blob for k in keys):
                    self._px.setProperty("voice", v.id)
                    log.info("pyttsx3 voice: %s", getattr(v, "name", v.id))
                    break
        except Exception as e:
            log.info("pyttsx3 voice select skipped: %s", e)

    def _speak_pyttsx3(self, text: str):
        self._px.say(text)
        self._px.runAndWait()

    @staticmethod
    def _play_wav(path: str):
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
        except ImportError:
            subprocess.run(["aplay", path], capture_output=True)

    # ---- synth to wav (for tablet/browser playback) ----
    def synth(self, text: str, out_path: str) -> bool:
        """Write speech to a wav file. Returns False if no engine can."""
        text = prepare_for_speech(text)
        try:
            if self.engine == "kokoro":
                import numpy as np
                import soundfile as sf
                chunks = [a for _, _, a in self._kokoro(text, voice=self._voice)]
                sf.write(out_path, np.concatenate(chunks), 24000)
                return True
            if self.engine == "piper":
                subprocess.run(["piper", "--model", self._piper_model,
                                "--output_file", out_path],
                               input=text.encode(), check=True, capture_output=True)
                return True
            if self.engine == "pyttsx3":
                self._px.save_to_file(text, out_path)
                self._px.runAndWait()
                return True
        except Exception as e:
            log.error("TTS synth failed (%s): %s", self.engine, e)
        log.info("[TTS] %s", text)
        return False

    # ---- speak on this machine's speakers (voice.output: sim_pc) ----
    def speak(self, text: str):
        text = prepare_for_speech(text)
        if not self.engine:
            log.info("[TTS] %s", text)
            return
        try:
            getattr(self, f"_speak_{self.engine}")(text)
        except Exception as e:
            log.error("TTS failed (%s): %s", self.engine, e)
            log.info("[TTS] %s", text)
