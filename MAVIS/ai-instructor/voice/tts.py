"""Local TTS behind one interface. engine: auto tries kokoro -> pyttsx3 -> console.
All engines run fully offline. speak() is blocking; call via executor.

Latency (voice: in config.yaml):
  tts_device       auto | cuda | cpu  -- Kokoro is small (82M): on a modern CPU it still runs
                   several times faster than real time, which leaves the GPU to MSFS + the LLM
  tts_cpu_threads  torch threads for Kokoro on the CPU (0 = torch's default)
A warm-up line is synthesised at start-up (the first real line would otherwise pay for it),
and split_sentences() lets the caller start playing the first sentence while the rest is made."""
from __future__ import annotations
import logging
import re
import subprocess
import tempfile

from voice.units import spoken_units

log = logging.getLogger(__name__)

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def split_sentences(text: str, min_chars: int = 40) -> list[str]:
    """Sentences to synthesise one by one; short ones are joined to the next so a
    line isn't chopped into tiny clips (each one costs a little gap)."""
    out: list[str] = []
    for s in _SENTENCE.split((text or "").strip()):
        if out and len(out[-1]) < min_chars:
            out[-1] = f"{out[-1]} {s}"
        else:
            out.append(s)
    if len(out) > 1 and len(out[-1]) < min_chars // 2:      # a short tail rides with the one before
        tail = out.pop()
        out[-1] = f"{out[-1]} {tail}"
    return [s for s in out if s.strip()]


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
    return spoken_units(out)       # "70kt" -> "70 knots", "25°" -> "25 degrees", "rpm" -> "R P M"


class TTS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.engine = None
        self.device = None
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
        device = str(self.cfg.get("tts_device") or "auto").lower()
        device = None if device == "auto" else device
        if device == "cpu" and int(self.cfg.get("tts_cpu_threads") or 0) > 0:
            import torch
            torch.set_num_threads(int(self.cfg["tts_cpu_threads"]))
        try:
            self._kokoro = KPipeline(lang_code="a", device=device)
        except TypeError:                      # kokoro < 0.9: no device argument
            self._kokoro = KPipeline(lang_code="a")
        except RuntimeError as e:              # asked for cuda, none there
            log.warning("Kokoro on %s failed (%s): using the CPU", device, e)
            self._kokoro = KPipeline(lang_code="a", device="cpu")
        self._voice = self.cfg.get("kokoro_voice", "af_heart")
        model = getattr(self._kokoro, "model", None)
        self.device = str(getattr(model, "device", "cpu"))
        # the first synthesis loads the voice and warms the kernels (seconds): pay it now
        import time
        t0 = time.perf_counter()
        for _ in self._kokoro("Ready.", voice=self._voice):
            pass
        log.info("Kokoro on %s, warm-up %.2fs", self.device, time.perf_counter() - t0)

    def _kokoro_audio(self, text: str):
        import numpy as np
        chunks = [a.cpu().numpy() if hasattr(a, "cpu") else a for _, _, a in self._kokoro(text, voice=self._voice)]
        return np.concatenate(chunks) if chunks else None

    def _speak_kokoro(self, text: str):
        import sounddevice as sd
        # play each sentence as soon as it's made; the next one is made while this one plays
        for part in split_sentences(text):
            audio = self._kokoro_audio(part)
            if audio is None:
                continue
            sd.wait()
            sd.play(audio, 24000)
        sd.wait()

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
                import soundfile as sf
                audio = self._kokoro_audio(text)
                if audio is None:
                    return False
                sf.write(out_path, audio, 24000)
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
