<#
.SYNOPSIS
    Brings up the whole MAVIS + IRIS stack in order: Postgres -> MSFS check
    -> MAVIS -> IRIS backend -> (optional) OBS/Smart-Eye adapter -> reverse
    proxy. See docs/INTEGRATION_PLAN.md for the architecture this wires
    together.

.DESCRIPTION
    Every step is a health-checked warning, not a hard stop -- a missing or
    unhealthy component is reported and the script moves on, so the
    dashboard still comes up in whatever degraded form it can (this is a
    hard requirement of the integration, not a shortcut: the frontend is
    built to show "service unavailable" banners rather than crash when a
    backend is down, and this launcher is the same philosophy applied to
    startup). The one exception is a component whose venv doesn't exist at
    all -- there's nothing to poll for, so it's skipped with a one-time
    setup pointer instead of an infinite retry loop.

    Each service's own venv is expected to already exist (see README.md for
    one-time setup per component) -- this script starts things, it doesn't
    install them.

.PARAMETER Port
    Port the reverse proxy (the single URL you actually open) listens on.

.PARAMETER SkipAdapter
    Don't launch the OBS/Smart-Eye adapter. Use this if you're driving it
    yourself, or don't want video/eye-tracking for this session -- IRIS's
    own "Go Live" button can still start it later (see routers/adapter.py).

.PARAMETER SkipMsfsCheck
    Skip the advisory MSFS process check (e.g. you're intentionally running
    MAVIS in --sim mock/replay mode with no MSFS at all).

.PARAMETER DryRun
    Print what would run (paths, commands, ports) without starting or
    health-checking anything. Useful for verifying the script on a machine
    that doesn't have all the venvs set up yet.

.EXAMPLE
    ./scripts/launch_all.ps1
    ./scripts/launch_all.ps1 -SkipAdapter -Port 8080
#>
param(
    [int]$Port = 3000,
    [switch]$SkipAdapter,
    [switch]$SkipMsfsCheck,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs\launcher"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

$script:procs = @{}

function Test-Port {
    param([string]$ComputerName = "localhost", [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($ComputerName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000, $false)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Wait-HttpUp {
    param([string]$Url, [int]$TimeoutSec = 45)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-Backend {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$WorkDir,
        [Parameter(Mandatory)][string]$VenvPython,
        [Parameter(Mandatory)][string[]]$ArgList,
        [string]$HealthUrl,
        [string]$SetupHint
    )
    if (-not (Test-Path $VenvPython)) {
        Write-Warning "[$Name] venv not found at $VenvPython -- skipping."
        if ($SetupHint) { Write-Warning "  One-time setup: $SetupHint" }
        return
    }
    if ($DryRun) {
        Write-Host "[$Name] (dry run) would run: `"$VenvPython`" $($ArgList -join ' ')  (cwd: $WorkDir)"
        return
    }

    $outLog = Join-Path $LogDir "$Name.out.log"
    $errLog = Join-Path $LogDir "$Name.err.log"
    Write-Host "[$Name] starting... (logs: $outLog)"
    $p = Start-Process -FilePath $VenvPython -ArgumentList $ArgList -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -WindowStyle Hidden -PassThru
    $script:procs[$Name] = $p

    if ($HealthUrl) {
        if (Wait-HttpUp -Url $HealthUrl) {
            Write-Host "[$Name] up (pid $($p.Id))."
        } else {
            Write-Warning "[$Name] didn't answer $HealthUrl within the timeout (pid $($p.Id), check $errLog). Continuing anyway -- the dashboard will show it as unavailable rather than fail to start."
        }
    }
}

Write-Host "=== 1/6: Postgres ==="
if ($DryRun) {
    Write-Host "(dry run) would check localhost:5432, try to start a postgresql* service if down"
} elseif (Test-Port -Port 5432) {
    Write-Host "Postgres already up on :5432."
} else {
    Write-Warning "Postgres not reachable on :5432 yet -- looking for a local service to start..."
    $svc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($svc) {
        try {
            Start-Service $svc.Name -ErrorAction Stop
            Write-Host "Started service '$($svc.Name)'."
        } catch {
            Write-Warning "Couldn't start service '$($svc.Name)': $_"
        }
    } else {
        Write-Warning "No 'postgresql*' Windows service found -- start Postgres yourself (see README.md §1)."
    }
    $up = $false
    for ($i = 0; $i -lt 15; $i++) {
        if (Test-Port -Port 5432) { $up = $true; break }
        Start-Sleep -Seconds 2
    }
    if ($up) {
        Write-Host "Postgres is up."
    } else {
        Write-Warning "Postgres still not reachable. IRIS's backend needs it -- continuing anyway (MAVIS's own coaching doesn't depend on Postgres at all, so it'll still work); Review/Analytics/Leaderboard won't until this is fixed."
    }
}

Write-Host "`n=== 2/6: MSFS check (advisory) ==="
if ($SkipMsfsCheck) {
    Write-Host "Skipped (-SkipMsfsCheck)."
} elseif ($DryRun) {
    Write-Host "(dry run) would check for a running FlightSimulator.exe process"
} else {
    $msfs = Get-Process -Name "FlightSimulator" -ErrorAction SilentlyContinue
    if ($msfs) {
        Write-Host "MSFS is running."
    } else {
        Write-Warning "MSFS not detected. MAVIS's mock/replay sim adapters still work without it -- if you meant to fly live, start MSFS with a flight loaded first (MAVIS's own SimConnect connect will otherwise keep retrying)."
    }
}

Write-Host "`n=== 3/6: MAVIS ==="
Start-Backend -Name "MAVIS" `
    -WorkDir (Join-Path $RepoRoot "MAVIS\ai-instructor") `
    -VenvPython (Join-Path $RepoRoot "MAVIS\ai-instructor\.venv\Scripts\python.exe") `
    -ArgList @("main.py") `
    -HealthUrl "http://localhost:6945/" `
    -SetupHint "cd MAVIS\ai-instructor; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

Write-Host "`n=== 4/6: IRIS backend ==="
Start-Backend -Name "IRIS-backend" `
    -WorkDir (Join-Path $RepoRoot "backend") `
    -VenvPython (Join-Path $RepoRoot "backend\.venv\Scripts\python.exe") `
    -ArgList @("-m", "uvicorn", "app.main:app", "--port", "8000") `
    -HealthUrl "http://localhost:8000/health" `
    -SetupHint "cd backend; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

Write-Host "`n=== 5/6: OBS/Smart-Eye adapter (optional) ==="
if ($SkipAdapter) {
    Write-Host "Skipped (-SkipAdapter). You can still start it later from IRIS's Live page."
} else {
    Start-Backend -Name "Adapter" `
        -WorkDir (Join-Path $RepoRoot "Sim_Data_Adapter") `
        -VenvPython (Join-Path $RepoRoot "Sim_Data_Adapter\.venv\Scripts\python.exe") `
        -ArgList @("LiveAdapter.py") `
        -HealthUrl $null `
        -SetupHint "cd Sim_Data_Adapter; python -m venv .venv; .venv\Scripts\pip install obsws-python websocket-client"
    # No HealthUrl -- it has no HTTP endpoint of its own; it self-syncs to
    # MAVIS (see wait_for_mavis_session()) and its own lock file already
    # makes this safe to also trigger from IRIS's "Go Live" button.
}

Write-Host "`n=== 6/6: Reverse proxy ==="
Start-Backend -Name "Proxy" `
    -WorkDir (Join-Path $RepoRoot "proxy") `
    -VenvPython (Join-Path $RepoRoot "proxy\.venv\Scripts\python.exe") `
    -ArgList @("-m", "uvicorn", "app:app", "--port", "$Port") `
    -HealthUrl "http://localhost:$Port/health" `
    -SetupHint "cd proxy; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

if ($DryRun) {
    Write-Host "`n(dry run complete -- nothing was started)"
    exit 0
}

Write-Host "`n================================================================"
Write-Host " Dashboard:  http://localhost:$Port"
Write-Host " Logs:       $LogDir"
Write-Host " Press Ctrl+C to stop everything this script started."
Write-Host "================================================================"

try {
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    Write-Host "`nStopping..."
    foreach ($name in $script:procs.Keys) {
        $p = $script:procs[$name]
        if ($p -and -not $p.HasExited) {
            Write-Host "  stopping $name (pid $($p.Id))"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
