# Setting up live lip-sync (MuseTalk) on a new PC

These instructions are for an assistant (or a person) setting up MAVIS's live lip-sync on
a Windows PC with an NVIDIA GPU. Follow the steps in order. Run each check and confirm it
passes before going on. If a step fails, look in **Troubleshooting** at the bottom before
trying anything else.

## What this is

MAVIS (`MAVIS/ai-instructor`) can lip-sync its avatar live. It runs
[MuseTalk](https://github.com/TMElyralab/MuseTalk) (MIT) as a separate local server,
`tools/musetalk_live.py`, and sends it each sentence's audio. Back comes a short video of
the avatar saying that sentence, and the tablet plays it in step with the voice.

- **MAVIS starts and stops the server itself** (`voice/lipsync_live.py`), using the
  `avatar_lipsync` settings in `config.yaml`.
- **MuseTalk lives outside this repo.** It is about 10 GB and has its own Python
  environment, whose packages conflict with MAVIS's. Its default location is
  `C:\Users\<user>\lipsync\MuseTalk`.
- **Without it, MAVIS still works.** It logs a warning and the avatar shows a general
  talking loop (`intro.mp4`) while MAVIS speaks.

The working reference setup: an RTX 5090 laptop, Python 3.12, PyTorch 2.11.0 cu128, and
MuseTalk at commit `2536eaf`. That setup rendered about 0.3 s of video per second of
speech. An RTX 4090 runs the same PyTorch build.

## Prerequisites (check them all first)

Run these in PowerShell:

```powershell
nvidia-smi                       # an NVIDIA GPU; driver version must be 570 or newer (CUDA 12.8)
py -3.12 --version               # Python 3.12 must be installed (python.org installer, "py" launcher)
git --version
```

- **Driver older than 570:** ask the user to update it through the NVIDIA app first.
- **No Python 3.12:** ask the user to install it from python.org. Use the default install
  location, with the `py` launcher ticked.
- **Disk space:** about 11 GB free on C:.

In the rest of this file:

- `$MT` is the MuseTalk folder.
- `$REPO` is this repository's root (the folder that holds `MAVIS` and `frontend`).

Set both at the start of every new shell:

```powershell
$MT   = "$env:USERPROFILE\lipsync\MuseTalk"
$REPO = "<path to the IRIS repo>"          # e.g. C:\Users\<user>\Desktop\IRIS
$PY   = "$MT\.venv\Scripts\python.exe"
```

## Option A: copy the folder from the PC where it already works (fastest)

Use this only if the user can copy `C:\Users\<user>\lipsync\MuseTalk` from the working PC,
for example on a USB drive.

1. **Copy the folder to the same path on this PC.** If the Windows user name differs,
   any path works, but step 3 must then match it.
2. **Check that Python 3.12 is at the standard location:**
   `$env:LOCALAPPDATA\Programs\Python\Python312\python.exe`.
   - The copied `.venv` points at that interpreter (see `$MT\.venv\pyvenv.cfg`, lines
     `home = ...` and `executable = ...`).
   - If those lines name a different user's folder, edit them to this PC's Python 3.12
     folder. The `command = ...` line is informational only.
3. **Point `config.yaml` at the folder** (see **Configure MAVIS** below).
4. **Run the checks** in **Verify** below. If `torch.cuda.is_available()` is `False`, or
   any import fails, the copied environment doesn't fit this PC. Delete `$MT\.venv` and
   do Option B steps 3–6. The models and the prepared avatar in the copied folder can stay.

## Option B: fresh install

### 1. Get MuseTalk

```powershell
git clone https://github.com/TMElyralab/MuseTalk.git $MT
git -C $MT checkout 2536eaf
```

### 2. Create its own Python environment

```powershell
py -3.12 -m venv "$MT\.venv"
& $PY -m pip install --upgrade pip
```

### 3. Install PyTorch (CUDA 12.8 build, for RTX 30/40/50 cards)

```powershell
& $PY -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
& $PY -I -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Must print `True` and the GPU's name. Stop here if it doesn't.

### 4. Install MuseTalk's libraries

Do **not** use MuseTalk's `requirements.txt`. It pins tensorflow and gradio (not needed)
and versions that don't install on Python 3.12.

```powershell
& $PY -m pip install diffusers==0.30.2 accelerate==0.28.0 transformers==4.39.2 huggingface_hub==0.36.2 `
    librosa soundfile opencv-python einops omegaconf ffmpeg-python moviepy "imageio[ffmpeg]" `
    mmengine==0.10.7 mmcv-lite==2.1.0 json_tricks munkres scipy matplotlib pycocotools
& $PY -m pip install mmpose==1.1.0 mmdet==3.2.0 --no-deps
```

Why it's done this way:

- **`mmcv-lite` instead of `mmcv`:** full `mmcv` has to be compiled with a C++/CUDA
  compiler, which these PCs don't have. Version 2.1.0 is pinned because mmpose 1.1.0
  rejects anything newer.
- **`--no-deps` for mmpose and mmdet:** their dependencies `xtcocotools` and `chumpy` also
  need a C++ compiler. MuseTalk's inference never uses them.
- **mmdet only provides one backbone class.** `tools/musetalk_batch.py` loads just that
  class, CSPNeXt, without the rest of mmdet.

### 5. Add the xtcocotools stand-in

mmpose imports `xtcocotools` when it loads, but only its training datasets use it. Point
it at the prebuilt `pycocotools`:

```powershell
& $PY -I -c "
import pathlib, site
d = pathlib.Path(site.getsitepackages()[-1]) / 'xtcocotools'
d.mkdir(exist_ok=True)
(d / '__init__.py').write_text('\"\"\"Stand-in: mmpose imports xtcocotools for its datasets only; MuseTalk inference never uses it.\"\"\"\n')
for m in ('coco', 'cocoeval', 'mask'):
    (d / f'{m}.py').write_text(f'from pycocotools.{m} import *  # noqa\n')
print('ok', d)
"
& $PY -I -c "import sys; sys.path[:0]=[r'$MT']; from mmpose.apis import inference_topdown, init_model; print('mmpose ok')"
```

`site.getsitepackages()[-1]` must be `...\.venv\Lib\site-packages`. If it isn't, use that
folder directly.

### 6. Download the models (about 4 GB, all from huggingface.co)

```powershell
& $PY -I -c "
from huggingface_hub import hf_hub_download as get
M = r'$MT\models'
jobs = [('TMElyralab/MuseTalk', ['musetalkV15/musetalk.json', 'musetalkV15/unet.pth'], M),
        ('stabilityai/sd-vae-ft-mse', ['config.json', 'diffusion_pytorch_model.bin'], M + r'\sd-vae'),
        ('openai/whisper-tiny', ['config.json', 'pytorch_model.bin', 'preprocessor_config.json'], M + r'\whisper'),
        ('yzd-v/DWPose', ['dw-ll_ucoco_384.pth'], M + r'\dwpose'),
        ('ManyOtherFunctions/face-parse-bisent', ['79999_iter.pth', 'resnet18-5c106cde.pth'], M + r'\face-parse-bisent'),
        ('madebyollin/taesd', ['config.json', 'diffusion_pytorch_model.safetensors'], M + r'\taesd')]
for repo, files, dest in jobs:
    for f in files:
        print('ok', get(repo, f, local_dir=dest))
"
```

Check that these files exist under `$MT\models`, with roughly these sizes:

| File | Size |
|---|---|
| `musetalkV15\unet.pth` | 3.4 GB |
| `sd-vae\diffusion_pytorch_model.bin` | 335 MB |
| `whisper\pytorch_model.bin` | 151 MB |
| `dwpose\dw-ll_ucoco_384.pth` | 407 MB |
| `face-parse-bisent\79999_iter.pth` | 53 MB |
| `taesd\diffusion_pytorch_model.safetensors` | 10 MB |

### 7. Prepare the avatar (once, about a minute)

This finds the face in the first 8 s of the avatar's idle video and caches it under
`$MT\results\v15\avatars\mavis`. The face detector's weights download automatically on
first use, from adrianbulat.com.

```powershell
& $PY -I "$REPO\MAVIS\ai-instructor\tools\musetalk_batch.py" --musetalk $MT `
    --source "$REPO\frontend\public\media\avatar\idle.mp4" --prepare-only
```

The last line must be `avatar ready: ...\results\v15\avatars\mavis`.

**If the avatar video ever changes,** run this again with `--redo-avatar` added.

## Configure MAVIS

In `$REPO\MAVIS\ai-instructor\config.yaml`, under `avatar_lipsync:`:

```yaml
  live: true
  live_python: 'C:\Users\<user>\lipsync\MuseTalk\.venv\Scripts\python.exe'
  live_musetalk: 'C:\Users\<user>\lipsync\MuseTalk'
  live_port: 8790
  live_decoder: taesd
  live_max_wait_s: 4
```

Set the two paths to this PC's `$MT`.

`config.yaml` is tracked in git. If the user name differs from the other PC, this edit
shows up as a change. Tell the user, so they don't commit one PC's paths over the other's.

## Verify

1. **Rendering works, and how fast.** Make a test WAV from the intro video's sound, then
   run the benchmark:

   ```powershell
   $FF = & $PY -I -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
   & $FF -y -v error -i "$REPO\frontend\public\media\avatar\intro.mp4" -vn -ac 1 -ar 16000 "$env:TEMP\lipsync_test.wav"
   & $PY -I "$REPO\MAVIS\ai-instructor\tools\musetalk_live.py" --musetalk $MT --bench "$env:TEMP\lipsync_test.wav"
   ```

   - The last line reads `... = X s per second of speech`.
   - On an RTX 4090 or 5090, X should be about 0.3 or less.
   - The MP4 it names should show the avatar's mouth moving.
   - If X is above 1, see Troubleshooting.

2. **MAVIS starts it.** Start MAVIS as usual. Within about 40 s the log should show:

   ```
   live lip-sync: starting MuseTalk (...)
   musetalk: MuseTalk live: <GPU name>, avatar mavis (400 frames, ...)
   musetalk: MuseTalk live: warmed up in ...
   live lip-sync: ready after NN s
   ```

   After that, spoken lines log `TTS -> tablet ... , lip-synced`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `torch.cuda.is_available()` is `False` | Wrong PyTorch build or old driver. Driver must be 570 or newer; reinstall step 3 with the cu128 index URL. |
| `No module named 'xtcocotools'` | Step 5 not done, or the stand-in went into a different site-packages folder. |
| `MMCV==2.2.0 is used but incompatible` | `& $PY -m pip install mmcv-lite==2.1.0` |
| `CSPNeXt is not in the mmpose::model registry` | mmdet missing: `& $PY -m pip install mmdet==3.2.0 --no-deps`. `musetalk_batch.py` registers CSPNeXt from it. |
| `Weights only load failed` | PyTorch 2.6+ loads checkpoints in safe mode. The tools allowlist exactly what MuseTalk's checkpoints need. Don't switch to `weights_only=False`; report the type named in the error instead. |
| `UnicodeEncodeError ... '\u300c'` | Old copy of the tools. They switch the console output to UTF-8; pull the latest repo. |
| `No prepared avatar at ...` | Step 7 not done. |
| `live lip-sync: avatar_lipsync.live_python / live_musetalk not found` | Paths in `config.yaml` don't match `$MT`. |
| `live lip-sync too slow for say_N.wav` on every line | A render took over `live_max_wait_s`. Check the benchmark speed. First suspect another program using the GPU heavily; the laptop GPU in power-saving mode is another cause. |
| Port 8790 already in use by something else | Set `live_port` to another free port. |
| `Error no file named diffusion_pytorch_model.safetensors` in the log | Harmless. diffusers looks for that file first, then loads the `.bin`. |

## Files involved (in this repo)

- `MAVIS/ai-instructor/tools/musetalk_live.py`: the live server. Run with MuseTalk's Python.
- `MAVIS/ai-instructor/tools/musetalk_batch.py`: prepares the avatar, and can pre-render
  the scripted-dialogue clip library.
- `MAVIS/ai-instructor/voice/lipsync_live.py`: MAVIS's side. Starts and stops the server
  and requests videos.
- `MAVIS/ai-instructor/runtime/speech.py`: attaches each sentence's video to what the
  tablet plays.
- `frontend/src/live/components/AvatarBox.jsx`: plays the video over the idle loop, in
  step with the voice.
