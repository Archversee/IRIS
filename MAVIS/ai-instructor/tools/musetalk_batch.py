"""Lip-sync every sentence's WAV in avatar/lines/ with MuseTalk, in one go.

Not part of MAVIS itself: run it with MuseTalk's own Python, from anywhere
(tools/avatar_lines.py video does that through config.yaml avatar_lipsync):

  <MuseTalk>/.venv/Scripts/python.exe tools/musetalk_batch.py --musetalk <MuseTalk> \
      --lines <media>/avatar/lines --source <media>/avatar/idle.mp4

MuseTalk (https://github.com/TMElyralab/MuseTalk, MIT) re-draws the mouth on
frames of the avatar video to match the audio. Its models load once, the
avatar is prepared once (face found and masked on each frame, cached under
<MuseTalk>/results/v15/avatars/<avatar-id>), and then each WAV becomes
<key>.mp4 next to it -- silent: the tablet plays the WAV itself. Sentences
that already have an MP4 are skipped, so it can be stopped and run again.
Built on MuseTalk's scripts/realtime_inference.py (its Avatar class).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("--musetalk", required=True, help="the MuseTalk checkout")
ap.add_argument("--lines", required=True, help="the avatar/lines folder (WAVs in, MP4s out)")
ap.add_argument("--source", required=True, help="the avatar video the mouth is drawn onto")
ap.add_argument("--source-seconds", type=float, default=8.0,
                help="use only the start of the source (it ping-pongs, so long sentences still fit)")
ap.add_argument("--avatar-id", default="mavis")
ap.add_argument("--fps", type=int, default=25)
ap.add_argument("--batch-size", type=int, default=16)
ap.add_argument("--limit", type=int, default=0, help="only this many sentences (try a few first)")
ap.add_argument("--redo-avatar", action="store_true", help="prepare the avatar again (new source video)")
cli = ap.parse_args()
for _s in (sys.stdout, sys.stderr):            # MuseTalk prints CJK brackets; Windows consoles are cp1252
    _s.reconfigure(encoding="utf-8", errors="replace")

MT = Path(cli.musetalk).resolve()
LINES = Path(cli.lines).resolve()
SOURCE = Path(cli.source).resolve()
os.chdir(MT)                                   # MuseTalk loads its models from ./models, ./musetalk
sys.path[:0] = [str(MT), str(MT / "musetalk" / "utils")]

# ffmpeg: the one imageio-ffmpeg ships, if none is on PATH
if not shutil.which("ffmpeg"):
    import imageio_ffmpeg
    exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
    bin_dir = MT / ".ffmpeg"
    bin_dir.mkdir(exist_ok=True)
    if not (bin_dir / "ffmpeg.exe").exists():
        shutil.copy(exe, bin_dir / "ffmpeg.exe")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
FFMPEG = shutil.which("ffmpeg")

import torch  # noqa: E402
from transformers import WhisperModel  # noqa: E402


def _cspnext_only():
    """Register mmdet's CSPNeXt backbone (DWPose's config names it) without the
    rest of mmdet, whose models need mmcv's compiled ops (mmcv-lite has none)."""
    import importlib
    import types
    try:
        import mmdet
    except ImportError:
        return
    base = Path(mmdet.__file__).parent / "models"
    for name, sub in (("mmdet.models", ""), ("mmdet.models.layers", "layers"),
                      ("mmdet.models.backbones", "backbones")):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [str(base / sub)]
            sys.modules[name] = m
    sys.modules["mmdet.models.layers"].CSPLayer = \
        importlib.import_module("mmdet.models.layers.csp_layer").CSPLayer
    importlib.import_module("mmdet.models.backbones.cspnext")


_cspnext_only()


def _numpy_safe_globals():
    """torch>=2.6 loads checkpoints weights-only; DWPose's carries a little numpy
    metadata. Allow just those numpy types rather than turning the check off."""
    import numpy as np
    import numpy.dtypes
    from numpy.core.multiarray import _reconstruct, scalar
    # the pickle names numpy 1's paths; numpy 2 keeps them only as aliases
    allow = [(_reconstruct, "numpy.core.multiarray._reconstruct"),
             (scalar, "numpy.core.multiarray.scalar"), np.ndarray, np.dtype]
    allow += [c for c in vars(numpy.dtypes).values() if isinstance(c, type) and issubclass(c, np.dtype)]
    from mmengine.logging.history_buffer import HistoryBuffer   # DWPose's training log
    allow.append(HistoryBuffer)
    torch.serialization.add_safe_globals(allow)


_numpy_safe_globals()

import scripts.realtime_inference as rti  # noqa: E402
from musetalk.utils.audio_processor import AudioProcessor  # noqa: E402
from musetalk.utils.face_parsing import FaceParsing  # noqa: E402
from musetalk.utils.utils import load_all_model  # noqa: E402

# The face parser first loads ImageNet resnet18 weights, then its own checkpoint
# (79999_iter.pth), which holds every one of those layers again. The resnet18
# file is in torch's old .tar format, which can't be loaded weights-only, so
# skip that redundant first step.
for _name, _mod in list(sys.modules.items()):
    if _name.endswith("face_parsing.resnet") and hasattr(_mod, "Resnet18"):
        _mod.Resnet18.init_weight = lambda self, model_path: None

# the settings realtime_inference.py's own command line would give (v1.5)
rti.args = argparse.Namespace(
    version="v15", extra_margin=10, parsing_mode="jaw", left_cheek_width=90, right_cheek_width=90,
    audio_padding_length_left=2, audio_padding_length_right=2, skip_save_images=False,
    batch_size=cli.batch_size, fps=cli.fps)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"MuseTalk on {torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU (slow)'}")
vae, unet, pe = load_all_model(unet_model_path="./models/musetalkV15/unet.pth", vae_type="sd-vae",
                               unet_config="./models/musetalkV15/musetalk.json", device=device)
pe = pe.half().to(device)
vae.vae = vae.vae.half().to(device)
unet.model = unet.model.half().to(device)
weight_dtype = unet.model.dtype
whisper = WhisperModel.from_pretrained("./models/whisper").to(device=device, dtype=weight_dtype).eval()
whisper.requires_grad_(False)
rti.device, rti.vae, rti.unet, rti.pe = device, vae, unet, pe
rti.timesteps = torch.tensor([0], device=device)
rti.audio_processor = AudioProcessor(feature_extractor_path="./models/whisper")
rti.weight_dtype, rti.whisper = weight_dtype, whisper
rti.fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)

# the avatar: the start of the source video, at the clips' frame rate
avatar_dir = MT / "results" / "v15" / "avatars" / cli.avatar_id
if cli.redo_avatar and avatar_dir.exists():
    shutil.rmtree(avatar_dir)
prepare = not avatar_dir.exists()
src = SOURCE
if prepare:
    src = MT / "results" / f"{cli.avatar_id}_source.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(SOURCE), "-t", str(cli.source_seconds),
                    "-r", str(cli.fps), "-an", str(src)], check=True)
    print(f"preparing the avatar from {SOURCE.name} (first {cli.source_seconds:g} s) ...")
avatar = rti.Avatar(avatar_id=cli.avatar_id, video_path=str(src), bbox_shift=0,
                    batch_size=cli.batch_size, preparation=prepare)

# which sentences: the enabled ones in lines.json that have a WAV and no MP4 yet
listed = json.loads((LINES / "lines.json").read_text(encoding="utf-8"))["lines"]
todo = [e for e in listed if e.get("enabled", True)
        and (LINES / f"{e['key']}.wav").exists() and not (LINES / f"{e['key']}.mp4").exists()]
if cli.limit:
    todo = todo[:cli.limit]
print(f"{len(todo)} sentences to lip-sync")
t_all = time.time()
for i, e in enumerate(todo, 1):
    t0 = time.time()
    k = e["key"]
    avatar.inference(str(LINES / f"{k}.wav"), k, cli.fps, False)
    made = Path(avatar.video_out_path) / f"{k}.mp4"
    if not made.exists():
        print(f"[{i}/{len(todo)}] FAILED: {e['text'][:60]}")
        continue
    # keep the picture only: the WAV next to it is what's played
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(made), "-an", "-c:v", "copy",
                    "-movflags", "+faststart", str(LINES / f"{k}.mp4")], check=True)
    made.unlink()
    print(f"[{i}/{len(todo)}] {time.time() - t0:5.1f}s  {e['text'][:60]}", flush=True)
print(f"done: {len(todo)} in {time.time() - t_all:.0f} s")
