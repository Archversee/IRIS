"""Speaker enrolment / verification, ECAPA-TDNN preferred, spectral fallback."""
from __future__ import annotations
import json
import logging
import re
import time
from pathlib import Path

import numpy as np

from voice.audio_util import TARGET_SR

log = logging.getLogger(__name__)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:48]
    return base or "unnamed"


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-8:
        return v.astype(np.float32)
    return (v / n).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def _spectral_embed(pcm: np.ndarray, sr: int = TARGET_SR) -> np.ndarray:
    """Lightweight offline voice print (weaker than ECAPA; last-resort fallback)."""
    wav = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if wav.size < 256:
        return _l2(np.zeros(64, dtype=np.float32))
    # Pre-emphasis + framing
    wav = np.append(wav[0], wav[1:] - 0.97 * wav[:-1])
    frame = int(0.025 * sr)
    hop = int(0.010 * sr)
    n = 1 + max(0, (wav.size - frame) // hop)
    feats = []
    window = np.hanning(frame).astype(np.float32)
    for i in range(n):
        start = i * hop
        chunk = wav[start:start + frame]
        if chunk.size < frame:
            chunk = np.pad(chunk, (0, frame - chunk.size))
        spec = np.abs(np.fft.rfft(chunk * window)) + 1e-8
        log_spec = np.log(spec)
        # 32 mel-ish bands by averaging FFT bins
        bands = np.array_split(log_spec, 32)
        band_e = np.array([b.mean() for b in bands], dtype=np.float32)
        feats.append(band_e)
    mat = np.stack(feats, axis=0)
    mean = mat.mean(axis=0)
    std = mat.std(axis=0)
    return _l2(np.concatenate([mean, std]).astype(np.float32))


class SpeakerEngine:
    """Lazy-loaded embedding backend."""

    def __init__(self):
        self.backend: str | None = None
        self._ready = False

    def ensure(self) -> bool:
        if self._ready:
            return True
        try:
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            savedir = Path(__file__).resolve().parent.parent / "data" / "models" / "ecapa"
            savedir.mkdir(parents=True, exist_ok=True)
            self._ecapa = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(savedir),
                run_opts={"device": device},
                local_strategy=LocalStrategy.COPY,
            )
            self._torch = torch
            self.backend = "ecapa"
            self._ready = True
            log.info("Speaker backend: ECAPA-TDNN (%s)", device)
            return True
        except Exception as e:
            log.info("ECAPA unavailable (%s); using spectral fallback", e)
        self.backend = "spectral"
        self._ready = True
        log.info("Speaker backend: spectral fallback")
        return True

    def embed(self, pcm: np.ndarray) -> np.ndarray | None:
        if not self.ensure():
            return None
        wav = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if self.backend == "ecapa":
            torch = self._torch
            try:
                with torch.no_grad():
                    tensor = torch.from_numpy(wav).float().unsqueeze(0)
                    emb = self._ecapa.encode_batch(tensor)
                    vec = emb.squeeze().detach().cpu().numpy().astype(np.float32)
                return _l2(vec)
            except Exception as e:
                log.warning("ECAPA embed failed (%s); spectral fallback", e)
                return _spectral_embed(wav)
        return _spectral_embed(wav)


class VoiceStore:
    def __init__(self, root: Path, threshold: float = 0.30):
        self.root = Path(root)
        self.emb_dir = self.root / "embeddings"
        self.index_path = self.root / "voices.json"
        self.threshold = float(threshold)
        self.engine = SpeakerEngine()
        self.root.mkdir(parents=True, exist_ok=True)
        self.emb_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict = {}
        self._load()

    def _load(self) -> None:
        if self.index_path.is_file():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._index = data
            except Exception:
                self._index = {}

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def lookup(self, name: str) -> dict | None:
        key = name.strip().lower()
        if not key:
            return None
        row = self._index.get(key)
        if not row:
            return None
        return {
            "enrolled": True,
            "name": row.get("name") or name,
            "ts": row.get("ts"),
            "backend": row.get("backend"),
        }

    def enrol(self, name: str, pcm: np.ndarray) -> tuple[bool, str, float | None]:
        display = name.strip()[:48]
        if not display:
            return False, "no_name", None
        if not self.engine.ensure():
            return False, "backend_unavailable", None
        emb = self.engine.embed(pcm)
        if emb is None:
            return False, "embed_failed", None
        key = display.lower()
        slug = _slug(display)
        fname = f"{slug}.npy"
        np.save(self.emb_dir / fname, emb)
        self._index[key] = {
            "name": display,
            "ts": int(time.time() * 1000),
            "embedding_file": fname,
            "backend": self.engine.backend,
        }
        self._save()
        return True, "ok", 1.0

    def verify(self, name: str, pcm: np.ndarray) -> tuple[bool, float | None, str]:
        """Return (accepted, similarity, reason). Not enrolled → accept (no gate)."""
        key = name.strip().lower()
        row = self._index.get(key)
        if not row:
            return True, None, "not_enrolled"
        if not self.engine.ensure():
            return True, None, "backend_unavailable"
        path = self.emb_dir / row["embedding_file"]
        if not path.is_file():
            return True, None, "missing_embedding"
        try:
            enrolled = np.load(path)
        except Exception:
            return True, None, "missing_embedding"
        emb = self.engine.embed(pcm)
        if emb is None:
            return False, None, "embed_failed"
        if enrolled.shape != emb.shape:
            log.warning("embedding dim mismatch enrolled=%s vs %s, accepting",
                        enrolled.shape, emb.shape)
            return True, None, "dim_mismatch"
        sim = cosine(_l2(enrolled.astype(np.float32)), emb)
        if sim >= self.threshold:
            return True, sim, "ok"
        return False, sim, "speaker_reject"

    def remove(self, name: str) -> bool:
        key = name.strip().lower()
        row = self._index.pop(key, None)
        if not row:
            return False
        path = self.emb_dir / row.get("embedding_file", "")
        if path.is_file():
            try:
                path.unlink()
            except Exception:
                pass
        self._save()
        return True
