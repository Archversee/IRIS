"""Helper steps for tools/setup_lipsync.ps1, run with MuseTalk's own Python (-I).

  shim                                   xtcocotools stand-in (see docs/LIPSYNC_SETUP.md step 5)
  models  --musetalk DIR                 download the models (skips files already there)
  config  --config FILE --python EXE --musetalk DIR
                                         point config.yaml's avatar_lipsync at this PC's MuseTalk
  testwav --source MP4 --out WAV         a 16 kHz mono WAV from a video's sound (for --bench)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

MODELS = [
    ("TMElyralab/MuseTalk", ["musetalkV15/musetalk.json", "musetalkV15/unet.pth"], ""),
    ("stabilityai/sd-vae-ft-mse", ["config.json", "diffusion_pytorch_model.bin"], "sd-vae"),
    ("openai/whisper-tiny", ["config.json", "pytorch_model.bin", "preprocessor_config.json"], "whisper"),
    ("yzd-v/DWPose", ["dw-ll_ucoco_384.pth"], "dwpose"),
    ("ManyOtherFunctions/face-parse-bisent", ["79999_iter.pth", "resnet18-5c106cde.pth"], "face-parse-bisent"),
    ("madebyollin/taesd", ["config.json", "diffusion_pytorch_model.safetensors"], "taesd"),
]


def shim(_a) -> None:
    d = Path(sysconfig.get_paths()["purelib"]) / "xtcocotools"
    d.mkdir(exist_ok=True)
    (d / "__init__.py").write_text('"""Stand-in: mmpose imports xtcocotools for its datasets only; '
                                   'MuseTalk inference never uses it."""\n', encoding="utf-8")
    for m in ("coco", "cocoeval", "mask"):
        (d / f"{m}.py").write_text(f"from pycocotools.{m} import *  # noqa\n", encoding="utf-8")
    print(f"xtcocotools stand-in: {d}")


def models(a) -> None:
    from huggingface_hub import hf_hub_download
    root = Path(a.musetalk) / "models"
    for repo, files, sub in MODELS:
        dest = root / sub if sub else root
        for f in files:
            have = dest / f
            if have.exists() and have.stat().st_size > 0:
                print(f"  have  {have.relative_to(root)}  ({have.stat().st_size / 1e6:,.0f} MB)")
                continue
            print(f"  get   {repo}/{f} ...", flush=True)
            p = Path(hf_hub_download(repo, f, local_dir=str(dest)))
            print(f"  ok    {p.relative_to(root)}  ({p.stat().st_size / 1e6:,.0f} MB)")


def config(a) -> None:
    p = Path(a.config)
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "avatar_lipsync:")
    except StopIteration:
        sys.exit(f"{p}: no avatar_lipsync: section (pull the latest repo)")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and not lines[i].startswith((" ", "#"))), len(lines))
    want = {"live": "true", "live_python": f"'{a.python}'", "live_musetalk": f"'{a.musetalk}'"}
    changed = []
    for key, val in want.items():
        pat = re.compile(rf"^(\s+){key}:\s*([^#]*?)(\s*#.*)?$")
        for i in range(start + 1, end):
            m = pat.match(lines[i])
            if m:
                if m.group(2).strip() != val:
                    lines[i] = f"{m.group(1)}{key}: {val}{m.group(3) or ''}"
                    changed.append(key)
                break
        else:                                      # not there yet: add it at the section's end
            lines.insert(end, f"  {key}: {val}")
            end += 1
            changed.append(key)
    if changed:
        p.write_bytes(nl.join(lines).encode("utf-8"))
    import yaml
    sec = yaml.safe_load(p.read_text(encoding="utf-8"))["avatar_lipsync"]
    print(f"config.yaml: live={sec.get('live')}  live_python={sec.get('live_python')}  "
          f"live_musetalk={sec.get('live_musetalk')}" + (f"  (changed: {', '.join(changed)})" if changed
                                                         else "  (already set)"))


def testwav(a) -> None:
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-i", a.source, "-vn",
                    "-ac", "1", "-ar", "16000", a.out], check=True)
    print(f"test audio: {a.out}")


ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
sub = ap.add_subparsers(dest="cmd", required=True)
sub.add_parser("shim").set_defaults(fn=shim)
m = sub.add_parser("models")
m.add_argument("--musetalk", required=True)
m.set_defaults(fn=models)
c = sub.add_parser("config")
c.add_argument("--config", required=True)
c.add_argument("--python", required=True)
c.add_argument("--musetalk", required=True)
c.set_defaults(fn=config)
t = sub.add_parser("testwav")
t.add_argument("--source", required=True)
t.add_argument("--out", required=True)
t.set_defaults(fn=testwav)
args = ap.parse_args()
args.fn(args)
