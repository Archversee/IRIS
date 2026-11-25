<#
Sets up MAVIS's live lip-sync (MuseTalk) on this PC, start to finish.
The same steps as docs/LIPSYNC_SETUP.md, done for you. Safe to run again:
anything already done is skipped, so after a failure just fix the cause and rerun.

Run it by double-clicking tools\setup_lipsync.bat, or from PowerShell:
  powershell -ExecutionPolicy Bypass -File MAVIS\ai-instructor\tools\setup_lipsync.ps1
Options:
  -MuseTalkDir <folder>   where MuseTalk goes (default: <your user folder>\lipsync\MuseTalk)
  -SkipBench              skip the speed test at the end
  -NoConfig               don't touch config.yaml
#>
param(
    [string]$MuseTalkDir = "$env:USERPROFILE\lipsync\MuseTalk",
    [switch]$SkipBench,
    [switch]$NoConfig
)

$ErrorActionPreference = "Continue"      # native tools write progress to stderr; exit codes are checked instead
$MuseTalkCommit = "2536eaf"
$Tools = $PSScriptRoot                                  # ...\MAVIS\ai-instructor\tools
$AI = Split-Path $Tools -Parent                         # ...\MAVIS\ai-instructor
$Repo = Split-Path (Split-Path $AI -Parent) -Parent     # the IRIS repo
$MT = $MuseTalkDir
$PY = Join-Path $MT ".venv\Scripts\python.exe"
$Total = 9

function Step([int]$n, [string]$text) { Write-Host ""; Write-Host "[$n/$Total] $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "  OK  $text" -ForegroundColor Green }
function Info([string]$text) { Write-Host "      $text" }
function Fail([string]$text) {
    Write-Host ""
    Write-Host "STOPPED: $text" -ForegroundColor Red
    Write-Host "Fix that, then run this script again (finished steps are skipped)."
    Write-Host "More help: the Troubleshooting table in MAVIS\ai-instructor\docs\LIPSYNC_SETUP.md"
    exit 1
}
function Run([string]$exe, [string[]]$argList, [string]$what) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)." }
}

Write-Host "MAVIS live lip-sync setup" -ForegroundColor White
Info "IRIS repo:  $Repo"
Info "MuseTalk:   $MT"
if (-not (Test-Path (Join-Path $Repo "frontend\public\media\avatar\idle.mp4"))) {
    Fail "This script must stay in MAVIS\ai-instructor\tools of the IRIS repo (couldn't find frontend\public\media\avatar\idle.mp4)."
}

# ---------------------------------------------------------------------------
Step 1 "Checking this PC"
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $smi) { Fail "No NVIDIA driver found (nvidia-smi). Install the latest driver from the NVIDIA App." }
$gpu = (& nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | Select-Object -First 1)
if (-not $gpu) { Fail "nvidia-smi didn't report a GPU." }
$gpuName, $driver = $gpu.Split(",") | ForEach-Object { $_.Trim() }
$driverMajor = [int]($driver.Split(".")[0])
if ($driverMajor -lt 570) { Fail "NVIDIA driver $driver is too old: need 570 or newer (CUDA 12.8). Update it in the NVIDIA App." }
Ok "GPU: $gpuName, driver $driver"

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyLauncher) { Fail "Python launcher 'py' not found. Install Python 3.12 from python.org (tick 'py launcher')." }
$pyVer = (& py -3.12 -c "import sys; print(sys.version.split()[0])" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $pyVer) { Fail "Python 3.12 not found. Install Python 3.12 from python.org (default location, tick 'py launcher')." }
Ok "Python $pyVer"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git not found. Install it from git-scm.com." }
Ok "Git"

$drive = (Split-Path $MT -Qualifier).TrimEnd(":")
$freeGB = [math]::Round((Get-PSDrive $drive).Free / 1GB, 1)
if (-not (Test-Path $PY) -and $freeGB -lt 11) { Fail "Only $freeGB GB free on ${drive}: -- need about 11 GB." }
Ok "$freeGB GB free on ${drive}:"

# ---------------------------------------------------------------------------
Step 2 "MuseTalk code"
if (Test-Path (Join-Path $MT "scripts\realtime_inference.py")) {
    Ok "already there"
} else {
    New-Item -ItemType Directory -Force (Split-Path $MT -Parent) | Out-Null
    Run git @("clone", "https://github.com/TMElyralab/MuseTalk.git", $MT) "Downloading MuseTalk"
    Run git @("-C", $MT, "checkout", "-q", $MuseTalkCommit) "Checking out MuseTalk $MuseTalkCommit"
    Ok "downloaded ($MuseTalkCommit)"
}

# ---------------------------------------------------------------------------
Step 3 "MuseTalk's own Python environment"
if (Test-Path $PY) {
    & $PY -c "import sys" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail "$MT\.venv exists but won't start (copied from another PC?). Delete the .venv folder and rerun."
    }
    Ok "already there"
} else {
    Run py @("-3.12", "-m", "venv", (Join-Path $MT ".venv")) "Creating the environment"
    Run $PY @("-m", "pip", "install", "--upgrade", "pip", "-q") "Updating pip"
    Ok "created"
}

# ---------------------------------------------------------------------------
Step 4 "PyTorch for the GPU (about 3 GB the first time)"
Run $PY @("-m", "pip", "install", "-q", "torch==2.11.0", "torchvision==0.26.0", "torchaudio==2.11.0",
          "--index-url", "https://download.pytorch.org/whl/cu128") "Installing PyTorch"
$cuda = (& $PY -I -c "import torch; print(torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" 2>$null)
if (-not ($cuda -like "True*")) { Fail "PyTorch can't use the GPU ($cuda). Check the NVIDIA driver (570+)." }
Ok "PyTorch sees $($cuda.Split('|')[1].Trim())"

# ---------------------------------------------------------------------------
Step 5 "MuseTalk's libraries"
Run $PY @("-m", "pip", "install", "-q", "diffusers==0.30.2", "accelerate==0.28.0", "transformers==4.39.2",
          "huggingface_hub==0.36.2", "librosa", "soundfile", "opencv-python", "einops", "omegaconf",
          "ffmpeg-python", "moviepy", "imageio[ffmpeg]", "mmengine==0.10.7", "mmcv-lite==2.1.0",
          "json_tricks", "munkres", "scipy", "matplotlib", "pycocotools", "pyyaml") "Installing libraries"
# their own dependencies need a C++ compiler and aren't used: see docs/LIPSYNC_SETUP.md step 4
Run $PY @("-m", "pip", "install", "-q", "mmpose==1.1.0", "mmdet==3.2.0", "--no-deps") "Installing mmpose/mmdet"
Run $PY @("-I", (Join-Path $Tools "musetalk_setup.py"), "shim") "Adding the xtcocotools stand-in"
Ok "installed"

# ---------------------------------------------------------------------------
Step 6 "AI models (about 4 GB the first time)"
Run $PY @("-I", (Join-Path $Tools "musetalk_setup.py"), "models", "--musetalk", $MT) "Downloading the models"
Ok "all models present"

# ---------------------------------------------------------------------------
Step 7 "Preparing MAVIS's face for MuseTalk"
if (Test-Path (Join-Path $MT "results\v15\avatars\mavis\latents.pt")) {
    Ok "already prepared"
} else {
    Info "(about a minute; the face detector downloads its weights on first use)"
    Run $PY @("-I", (Join-Path $Tools "musetalk_batch.py"), "--musetalk", $MT,
              "--source", (Join-Path $Repo "frontend\public\media\avatar\idle.mp4"), "--prepare-only") "Preparing the avatar"
    Ok "prepared"
}

# ---------------------------------------------------------------------------
Step 8 "Pointing MAVIS's config.yaml at this MuseTalk"
if ($NoConfig) {
    Info "skipped (-NoConfig)"
} else {
    Run $PY @("-I", (Join-Path $Tools "musetalk_setup.py"), "config", "--config", (Join-Path $AI "config.yaml"),
              "--python", $PY, "--musetalk", $MT) "Updating config.yaml"
    Ok "config.yaml set"
}

# ---------------------------------------------------------------------------
Step 9 "Speed test"
if ($SkipBench) {
    Info "skipped (-SkipBench)"
} else {
    $wav = Join-Path $env:TEMP "mavis_lipsync_test.wav"
    Run $PY @("-I", (Join-Path $Tools "musetalk_setup.py"), "testwav",
              "--source", (Join-Path $Repo "frontend\public\media\avatar\intro.mp4"), "--out", $wav) "Making test audio"
    $out = Join-Path $env:TEMP "mavis_lipsync_test.mp4"
    $bench = & $PY -I (Join-Path $Tools "musetalk_live.py") --musetalk $MT --bench $wav --bench-out $out 2>$null
    if ($LASTEXITCODE -ne 0) { Fail "The test render failed. Run it by hand to see why:`n  & '$PY' -I '$Tools\musetalk_live.py' --musetalk '$MT' --bench '$wav'" }
    $line = $bench | Where-Object { $_ -match "per second of speech" } | Select-Object -Last 1
    Ok ($line -replace "\s*\{.*$", "" -replace " -> .*$", "")
    if ($line -match "= ([0-9.]+) s per second") {
        $x = [double]$Matches[1]
        if ($x -gt 1.0) { Write-Host "  !!  Slower than real time: MAVIS will often speak without lip-sync. Is another program using the GPU?" -ForegroundColor Yellow }
    }
    Info "test video: $out"
}

Write-Host ""
Write-Host "Done. Start MAVIS as usual: after about 40 s its log shows 'live lip-sync: ready'." -ForegroundColor Green
if (-not $NoConfig) {
    Write-Host "Note: config.yaml now has this PC's MuseTalk paths. If they differ from your other PC's, don't commit that change." -ForegroundColor Yellow
}
