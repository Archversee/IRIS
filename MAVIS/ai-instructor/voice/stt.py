"""Local speech-to-text via faster-whisper."""
from __future__ import annotations
import logging
import threading
import time

import numpy as np

from voice.audio_util import TARGET_SR

log = logging.getLogger(__name__)


class WhisperSTT:
    def __init__(self, model_size: str = "small", device: str = "auto",
                 compute_type: str = "auto", initial_prompt: str | None = None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.initial_prompt = (initial_prompt or "").strip() or None
        self._model = None
        self._lock = threading.Lock()
        self._err: str | None = None

    def ensure(self) -> bool:
        if self._model is not None:
            return True
        if self._err:
            return False
        try:
            from faster_whisper import WhisperModel
            device = self.device
            compute = self.compute_type
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    device = "cpu"
            if compute == "auto":
                compute = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(self.model_size, device=device, compute_type=compute)
            self._device_used = f"{device}/{compute}"
            log.info("Whisper model %s on %s (%s)", self.model_size, device, compute)
            if device == "cpu":
                log.warning("Whisper is on CPU. Expect ~10s per clip. For the GPU, "
                            "install CUDA torch: pip install torch --index-url "
                            "https://download.pytorch.org/whl/cu128  (plus "
                            "nvidia-cublas-cu12 nvidia-cudnn-cu12 for ctranslate2)")
            return True
        except Exception as e:
            self._err = str(e)
            log.warning("faster-whisper unavailable: %s", e)
            return False

    def transcribe(self, pcm: np.ndarray, language: str = "en") -> str:
        if not self.ensure() or self._model is None:
            return ""
        wav = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if wav.size < int(0.2 * TARGET_SR):
            return ""
        t0 = time.perf_counter()
        kw: dict = dict(
            language=language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        if self.initial_prompt:
            kw["initial_prompt"] = self.initial_prompt
        with self._lock:
            segments, _info = self._model.transcribe(wav, **kw)
            parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
        dt = time.perf_counter() - t0
        log.info("whisper: %.1fs audio -> %.2fs compute (%s)",
                 wav.size / TARGET_SR, dt, getattr(self, "_device_used", "?"))
        return " ".join(parts).strip()
