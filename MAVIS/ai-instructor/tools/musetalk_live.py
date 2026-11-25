"""MuseTalk lip-sync on demand: a small local server MAVIS sends each sentence to.

Not part of MAVIS itself: it runs with MuseTalk's own Python (its packages
clash with MAVIS's), and MAVIS starts it when config avatar_lipsync.live is on
(voice/lipsync_live.py):

  <MuseTalk>/.venv/Scripts/python.exe tools/musetalk_live.py --musetalk <MuseTalk> --port 8790

MuseTalk (https://github.com/TMElyralab/MuseTalk, MIT) re-draws the mouth on
frames of the avatar video to match the audio. The models load once and the
avatar is the one tools/musetalk_batch.py prepared (cached under
<MuseTalk>/results/v15/avatars/<avatar-id>; run that once first, e.g. with
--limit 1). Then each request turns a WAV into a silent MP4 of the avatar
saying it -- frames go straight from the GPU into the encoder, at a size made
for the tablet's avatar box, so a sentence takes a fraction of its own length.

  POST /render {"wav": path, "out": path}  -> {"ok": true, "secs": render time, "frames": n}
  GET  /health                             -> {"ok": true, ...} once the models are loaded

--bench WAV renders one file and prints the timing instead of serving.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pickle
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("--musetalk", required=True, help="the MuseTalk checkout")
ap.add_argument("--avatar-id", default="mavis")
ap.add_argument("--port", type=int, default=8790)
ap.add_argument("--fps", type=int, default=25)
ap.add_argument("--batch-size", type=int, default=16)
ap.add_argument("--scale", type=float, default=0.5, help="output size, as a fraction of the avatar video's")
ap.add_argument("--crf", type=int, default=23)
ap.add_argument("--decoder", choices=("taesd", "sd"), default="taesd",
                help="taesd: the tiny decoder (models/taesd), several times faster; sd: MuseTalk's own VAE")
ap.add_argument("--bench", help="render this WAV once, print the timing, exit")
ap.add_argument("--bench-out", help="where --bench writes its MP4 (default: next to the WAV)")
cli = ap.parse_args()
for _s in (sys.stdout, sys.stderr):            # MuseTalk prints CJK text; Windows consoles are cp1252
    _s.reconfigure(encoding="utf-8", errors="replace")

MT = Path(cli.musetalk).resolve()
os.chdir(MT)                                   # MuseTalk loads its models from ./models
sys.path[:0] = [str(MT), str(MT / "musetalk" / "utils")]

if not shutil.which("ffmpeg"):
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
else:
    FFMPEG = shutil.which("ffmpeg")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from transformers import WhisperModel  # noqa: E402

# Same compatibility shims as musetalk_batch.py (see there): torch>=2.6
# weights-only loading. Inference needs none of mmpose/DWPose here -- the
# avatar's landmarks were found when it was prepared.
import numpy.dtypes  # noqa: E402
from numpy.core.multiarray import _reconstruct, scalar  # noqa: E402

torch.serialization.add_safe_globals(
    [(_reconstruct, "numpy.core.multiarray._reconstruct"), (scalar, "numpy.core.multiarray.scalar"),
     np.ndarray, np.dtype]
    + [c for c in vars(numpy.dtypes).values() if isinstance(c, type) and issubclass(c, np.dtype)])

from musetalk.utils.audio_processor import AudioProcessor  # noqa: E402
from musetalk.utils.utils import datagen, load_all_model  # noqa: E402

FPS = cli.fps
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
t_load = time.time()
vae, unet, pe = load_all_model(unet_model_path="./models/musetalkV15/unet.pth", vae_type="sd-vae",
                               unet_config="./models/musetalkV15/musetalk.json", device=device)
pe = pe.half().to(device)
vae.vae = vae.vae.half().to(device)
unet.model = unet.model.half().to(device)
dtype = unet.model.dtype
whisper = WhisperModel.from_pretrained("./models/whisper").to(device=device, dtype=dtype).eval()
whisper.requires_grad_(False)
audio_processor = AudioProcessor(feature_extractor_path="./models/whisper")
timesteps = torch.tensor([0], device=device)

if cli.decoder == "taesd":
    # TAESD (https://github.com/madebyollin/taesd, MIT): a tiny decoder for the
    # same latents as the SD VAE MuseTalk uses -- most of a frame's GPU time
    # otherwise -- trading a little fine detail for speed. It takes the latents
    # as the UNet gives them (already scaled).
    from diffusers import AutoencoderTiny
    tiny = AutoencoderTiny.from_pretrained("./models/taesd", torch_dtype=dtype).to(device).eval()

    def decode_faces(pred):
        img = tiny.decode(pred.to(dtype)).sample          # -1..1, RGB
        img = ((img / 2 + 0.5).clamp(0, 1) * 255).round().to(torch.uint8)
        return img.permute(0, 2, 3, 1).flip(-1).cpu().numpy()   # BGR, like the frames
else:
    def decode_faces(pred):
        return vae.decode_latents(pred.to(dtype=vae.vae.dtype))

# ---- the prepared avatar: frames, face boxes, masks, face latents ----------
AV = MT / "results" / "v15" / "avatars" / cli.avatar_id
if not (AV / "latents.pt").exists():
    sys.exit(f"No prepared avatar at {AV}: run tools/musetalk_batch.py once (e.g. --limit 1) first.")


def _read_sorted(folder: Path, flags=cv2.IMREAD_COLOR):
    files = sorted(folder.glob("*.png"), key=lambda p: int(p.stem)) or \
        sorted(folder.glob("*.jpg"), key=lambda p: int(p.stem))
    return [cv2.imread(str(f), flags) for f in files]


latents = torch.load(AV / "latents.pt")
coords = pickle.loads((AV / "coords.pkl").read_bytes())
mask_boxes = pickle.loads((AV / "mask_coords.pkl").read_bytes())
frames = _read_sorted(AV / "full_imgs")
masks = _read_sorted(AV / "mask", cv2.IMREAD_GRAYSCALE)
# (saved already played forward then backward: the cycle MuseTalk's Avatar loops)
N = len(frames)
assert len(masks) == len(coords) == len(mask_boxes) == len(latents) == N, \
    (len(frames), len(masks), len(coords), len(mask_boxes), len(latents))
H, W = frames[0].shape[:2]
OUT_W = int(round(W * cli.scale / 2)) * 2      # even sizes for yuv420p
OUT_H = int(round(H * cli.scale / 2)) * 2
print(f"MuseTalk live: {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}, "
      f"avatar {cli.avatar_id} ({N} frames, {W}x{H} -> {OUT_W}x{OUT_H}), loaded in {time.time() - t_load:.0f} s",
      flush=True)


def _prepare(i: int):
    """Frame i at the output size, its face box there, and its mask cut down to
    where it isn't 0 (so a frame's blend touches only the lower face)."""
    sx, sy = OUT_W / W, OUT_H / H
    small = cv2.resize(frames[i], (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
    # the mask lives in its crop box, which can run past the frame's edge
    xs, ys, xe, ye = mask_boxes[i]
    full = np.zeros((H, W), np.uint8)
    cx0, cy0, cx1, cy1 = max(xs, 0), max(ys, 0), min(xe, W), min(ye, H)
    full[cy0:cy1, cx0:cx1] = masks[i][cy0 - ys:cy1 - ys, cx0 - xs:cx1 - xs]
    full = cv2.resize(full, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
    yy, xx = np.nonzero(full)
    box = (int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1) if len(xx) else (0, 0, 0, 0)
    alpha = (full[box[1]:box[3], box[0]:box[2]].astype(np.float32) / 255.0)[..., None]
    x1, y1, x2, y2 = coords[i]
    face_box = (max(int(round(x1 * sx)), 0), max(int(round(y1 * sy)), 0),
                min(int(round(x2 * sx)), OUT_W), min(int(round(y2 * sy)), OUT_H))
    return small, face_box, box, alpha


prepared = [_prepare(i) for i in range(N)]
del frames, masks

# Every render runs on this one thread -- one sentence at a time on the GPU, and
# CUDA/cuDNN's per-thread state (warmed up once) is kept between sentences;
# the HTTP server would otherwise use a fresh, cold thread per request.
_worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="musetalk")


def _blend(i: int, face: np.ndarray) -> np.ndarray:
    """Frame i of the avatar, at the output size, with the generated mouth area
    pasted in through its soft mask."""
    small, (fx1, fy1, fx2, fy2), (bx0, by0, bx1, by1), alpha = prepared[i]
    out = small.copy()
    pasted = small.copy()
    pasted[fy1:fy2, fx1:fx2] = cv2.resize(face, (fx2 - fx1, fy2 - fy1), interpolation=cv2.INTER_AREA)
    b = (slice(by0, by1), slice(bx0, bx1))
    out[b] = (pasted[b] * alpha + small[b] * (1.0 - alpha)).astype(np.uint8)
    return out


def render(wav: str, out: str) -> dict:
    return _worker.submit(_render, wav, out).result()


@torch.no_grad()
def _render(wav: str, out: str) -> dict:
    t0 = time.time()
    if True:
        feats, length = audio_processor.get_audio_feature(wav, weight_dtype=dtype)
        chunks = audio_processor.get_whisper_chunk(feats, device, dtype, whisper, length, fps=FPS,
                                                   audio_padding_length_left=2, audio_padding_length_right=2)
        tmp = f"{out}.part.mp4"
        enc = subprocess.Popen(
            [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(cli.crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        n = 0
        prof = {"audio": time.time() - t0, "gpu": 0.0, "blend": 0.0, "write": 0.0}
        try:
            bs = cli.batch_size
            for whisper_batch, latent_batch in datagen(chunks, latents, bs, device=device):
                tg = time.time()
                real = len(latent_batch)
                if real < bs:
                    # a sentence's last, short batch: pad it to the usual size, so the GPU
                    # always runs the one shape it was warmed up on
                    whisper_batch = torch.cat([whisper_batch, whisper_batch[-1:].expand(bs - real, *whisper_batch.shape[1:])])
                    latent_batch = torch.cat([latent_batch, latent_batch[-1:].expand(bs - real, *latent_batch.shape[1:])])
                audio_feat = pe(whisper_batch.to(device))
                pred = unet.model(latent_batch.to(device=device, dtype=dtype), timesteps,
                                  encoder_hidden_states=audio_feat).sample
                faces = decode_faces(pred[:real])
                prof["gpu"] += time.time() - tg
                for face in faces:
                    tb = time.time()
                    img = _blend(n % N, face.astype(np.uint8))
                    tw = time.time()
                    enc.stdin.write(img.tobytes())
                    prof["blend"] += tw - tb
                    prof["write"] += time.time() - tw
                    n += 1
            enc.stdin.close()
            err = enc.stderr.read().decode(errors="replace")
            if enc.wait() != 0:
                raise RuntimeError(f"encoder failed: {err[-300:]}")
        except BaseException:
            enc.kill()
            enc.wait()                         # (Windows: the file stays locked until it has exited)
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        os.replace(tmp, out)
    return {"ok": True, "secs": round(time.time() - t0, 3), "frames": n,
            "prof": {k: round(v, 3) for k, v in prof.items()}}


def warm_up() -> None:
    """One throwaway render (of a second of silence), so the first real sentence
    doesn't pay for CUDA's kernel tuning: seconds otherwise."""
    import tempfile
    import wave
    with tempfile.TemporaryDirectory(prefix="musetalk_warm_") as d:
        wav = os.path.join(d, "warm.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(2 * 16000))
        t = time.time()
        render(wav, os.path.join(d, "warm.mp4"))
        print(f"MuseTalk live: warmed up in {time.time() - t:.1f} s", flush=True)


warm_up()

if cli.bench:
    out = cli.bench_out or str(Path(cli.bench).with_suffix(".live.mp4"))
    r = render(cli.bench, out)
    dur = r["frames"] / FPS
    print(f"{r['frames']} frames ({dur:.1f} s of speech) in {r['secs']:.2f} s "
          f"= {r['secs'] / dur:.2f} s per second of speech -> {out}  {r['prof']}")
    sys.exit(0)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"ok": True, "avatar": cli.avatar_id, "fps": FPS, "size": [OUT_W, OUT_H]})
        else:
            self._reply(404, {"ok": False})

    def do_POST(self):  # noqa: N802
        if self.path != "/render":
            return self._reply(404, {"ok": False})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self._reply(200, render(str(req["wav"]), str(req["out"])))
        except Exception as e:                 # noqa: BLE001 -- report it, keep serving
            self._reply(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):                 # quiet: MAVIS logs what matters
        pass


srv = ThreadingHTTPServer(("127.0.0.1", cli.port), Handler)
print(f"MuseTalk live: serving on http://127.0.0.1:{cli.port}", flush=True)
srv.serve_forever()
