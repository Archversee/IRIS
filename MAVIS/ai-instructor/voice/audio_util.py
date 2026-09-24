"""Load / trim mic WAV blobs for enrolment and PTT."""
from __future__ import annotations
import io
import logging
import wave

import numpy as np

log = logging.getLogger(__name__)

TARGET_SR = 16000


def load_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Return mono float32 PCM in [-1, 1] and sample rate from a WAV blob."""
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        pcm = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sw == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width: {sw}")
    if nch > 1:
        pcm = pcm.reshape(-1, nch).mean(axis=1)
    return pcm, sr


def resample_mono(pcm: np.ndarray, sr: int, target_sr: int = TARGET_SR) -> np.ndarray:
    if sr == target_sr:
        return pcm.astype(np.float32, copy=False)
    if pcm.size == 0:
        return pcm.astype(np.float32)
    duration = pcm.size / float(sr)
    n_out = max(1, int(round(duration * target_sr)))
    x_old = np.linspace(0.0, 1.0, num=pcm.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.float32)


def rms(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(pcm), dtype=np.float64)))


def voiced_fraction(pcm: np.ndarray, sr: int = TARGET_SR, thr: float = 0.015) -> float:
    """Fraction of short frames above an RMS energy threshold."""
    if pcm.size == 0:
        return 0.0
    frame = max(1, int(sr * 0.02))
    n = pcm.size // frame
    if n == 0:
        return 1.0 if rms(pcm) >= thr else 0.0
    frames = pcm[: n * frame].reshape(n, frame)
    energies = np.sqrt(np.mean(np.square(frames), axis=1))
    return float(np.mean(energies >= thr))


def trim_silence(pcm: np.ndarray, sr: int = TARGET_SR, thr: float = 0.012,
                 pad_s: float = 0.15) -> np.ndarray:
    """Energy-based trim (Silero optional; this is the always-on path)."""
    if pcm.size == 0:
        return pcm
    frame = max(1, int(sr * 0.02))
    n = max(1, pcm.size // frame)
    frames = pcm[: n * frame].reshape(n, frame)
    energies = np.sqrt(np.mean(np.square(frames), axis=1))
    active = np.where(energies >= thr)[0]
    if active.size == 0:
        return pcm[:0]
    pad = int(pad_s / 0.02)
    start = max(0, int(active[0]) - pad) * frame
    end = min(n, int(active[-1]) + 1 + pad) * frame
    return pcm[start:end].astype(np.float32, copy=False)


def prepare_utterance(data: bytes, min_s: float = 0.6, min_voiced: float = 0.12,
                      min_rms: float = 0.008) -> tuple[np.ndarray | None, str]:
    """Decode WAV → 16 kHz mono → trim. Returns (pcm, error_reason)."""
    try:
        pcm, sr = load_wav_bytes(data)
    except Exception as e:
        log.info("wav decode failed: %s", e)
        return None, "bad_audio"
    pcm = resample_mono(pcm, sr, TARGET_SR)
    pcm = trim_silence(pcm, TARGET_SR)
    if pcm.size < int(min_s * TARGET_SR):
        return None, "too_short"
    if rms(pcm) < min_rms or voiced_fraction(pcm, TARGET_SR) < min_voiced:
        return None, "silence"
    return pcm, ""


def to_wav_bytes(pcm: np.ndarray, sr: int = TARGET_SR) -> bytes:
    clipped = np.clip(pcm, -1.0, 1.0)
    ints = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(ints.tobytes())
    return buf.getvalue()
