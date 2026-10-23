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
$script:specs = @{}     # how each service was started, so the watchdog can start it again

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

# Services run in hidden windows, so closing this console (instead of
# Ctrl+C) leaves them running on whatever code they started with. A new
# copy then can't bind the port, while the stale one still answers the
# health check -- so it looks "up" but is old code. Stop any leftover
# listener on the port first.
function Stop-PortOwner {
    param([string]$Name, [int]$ListenPort)
    $owners = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $owners) {
        if (-not $procId) { continue }
        $old = Get-Process -Id $procId -ErrorAction SilentlyContinue
        Write-Warning "[$Name] port $ListenPort is already held by $($old.ProcessName) (pid $procId) -- probably left over from an earlier run. Stopping it so the current code starts."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    if ($owners) {
        for ($i = 0; $i -lt 20 -and (Test-Port -Port $ListenPort); $i++) { Start-Sleep -Milliseconds 250 }
    }
}

# Starts (or restarts) a service from the spec Start-Backend saved for it.
function Start-ServiceProcess {
    param([Parameter(Mandatory)][string]$Name)
    $spec = $script:specs[$Name]
    $p = Start-Process -FilePath $spec.VenvPython -ArgumentList $spec.ArgList -WorkingDirectory $spec.WorkDir `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") -RedirectStandardError (Join-Path $LogDir "$Name.err.log") `
        -WindowStyle Hidden -PassThru
    $null = $p.Handle      # keep the handle open, or ExitCode reads empty once it exits
    $script:procs[$Name] = $p
    return $p
}

# ---------------------------------------------------------------------
# Watchdog: once everything is up, a service whose process exits (a crash,
# or the backend giving up because Postgres wasn't reachable) is started
# again. Its logs from the run that died are kept as
# <Name>.{out,err}.crash-<time>.log, since a restart overwrites the live ones.
# Waits between restarts double (2 s .. 60 s) while it keeps dying, and reset
# once it has stayed up for RESTART_RESET_S.
# The OBS/Smart-Eye adapter isn't watched: IRIS's backend launches it for
# every MAVIS session itself, and it exits on purpose when a copy is running.
# ---------------------------------------------------------------------
$WATCHED = @("MAVIS", "IRIS-backend", "Proxy")
$RESTART_MIN_S = 2
$RESTART_MAX_S = 60
$RESTART_RESET_S = 120
$script:restartDelay = @{}
$script:restartAt = @{}

function Save-CrashLogs {
    param([string]$Name)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($kind in "out", "err") {
        $log = Join-Path $LogDir "$Name.$kind.log"
        if (Test-Path -LiteralPath $log) {
            Move-Item -LiteralPath $log -Destination (Join-Path $LogDir "$Name.$kind.crash-$stamp.log") -Force -ErrorAction SilentlyContinue
        }
    }
    return $stamp
}

function Invoke-Watchdog {
    foreach ($name in $WATCHED) {
        $p = $script:procs[$name]
        if (-not $p -or -not $script:specs.ContainsKey($name)) { continue }   # skipped at startup (no venv)
        if (-not $p.HasExited) {
            if ($script:restartDelay[$name] -and ((Get-Date) - $p.StartTime).TotalSeconds -gt $RESTART_RESET_S) {
                $script:restartDelay[$name] = 0       # stayed up: next crash restarts quickly again
            }
            continue
        }
        if (-not $script:restartAt[$name]) {
            $delay = [Math]::Max($RESTART_MIN_S, [Math]::Min($RESTART_MAX_S, 2 * [int]$script:restartDelay[$name]))
            $script:restartDelay[$name] = $delay
            $script:restartAt[$name] = (Get-Date).AddSeconds($delay)
            $stamp = Save-CrashLogs -Name $name
            Write-Warning ("[$name] exited (code $($p.ExitCode)) at $(Get-Date -Format 'HH:mm:ss') -- restarting in $delay s. " +
                "Its logs: $LogDir\$name.err.crash-$stamp.log")
        }
        if ((Get-Date) -ge $script:restartAt[$name]) {
            $script:restartAt[$name] = $null
            $port = $script:specs[$name].ListenPort
            if ($port) { Stop-PortOwner -Name $name -ListenPort $port }
            $np = Start-ServiceProcess -Name $name
            Write-Host "[$name] restarted (pid $($np.Id))."
        }
    }
}

function Start-Backend {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$WorkDir,
        [Parameter(Mandatory)][string]$VenvPython,
        [Parameter(Mandatory)][string[]]$ArgList,
        [string]$HealthUrl,
        [string]$SetupHint,
        [int]$ListenPort = 0
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
    if ($ListenPort) { Stop-PortOwner -Name $Name -ListenPort $ListenPort }

    $outLog = Join-Path $LogDir "$Name.out.log"
    $errLog = Join-Path $LogDir "$Name.err.log"
    Write-Host "[$Name] starting... (logs: $outLog)"
    $script:specs[$Name] = @{ WorkDir = $WorkDir; VenvPython = $VenvPython; ArgList = $ArgList; ListenPort = $ListenPort }
    $p = Start-ServiceProcess -Name $Name

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
    -ListenPort 6945 `
    -SetupHint "cd MAVIS\ai-instructor; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

Write-Host "`n=== 4/6: IRIS backend ==="
Start-Backend -Name "IRIS-backend" `
    -WorkDir (Join-Path $RepoRoot "backend") `
    -VenvPython (Join-Path $RepoRoot "backend\.venv\Scripts\python.exe") `
    -ArgList @("-m", "uvicorn", "app.main:app", "--port", "8000") `
    -HealthUrl "http://localhost:8000/health" `
    -ListenPort 8000 `
    -SetupHint "cd backend; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

Write-Host "`n=== 5/6: OBS/Smart-Eye adapter (optional) ==="
if ($SkipAdapter) {
    Write-Host "Skipped (-SkipAdapter). You can still start it later from IRIS's Live page."
} else {
    # No port to check -- and its lock file makes a new copy exit if an old
    # one is still running, so stop any leftover LiveAdapter.py first.
    if (-not $DryRun) {
        Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*LiveAdapter.py*" } |
            ForEach-Object {
                Write-Warning "[Adapter] stopping leftover LiveAdapter.py (pid $($_.ProcessId)) so the current code starts."
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
    }
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
    -ListenPort $Port `
    -SetupHint "cd proxy; python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"

if ($DryRun) {
    Write-Host "`n(dry run complete -- nothing was started)"
    exit 0
}

Write-Host "`n================================================================"
Write-Host " Dashboard:  http://localhost:$Port"
Write-Host " Logs:       $LogDir"
Write-Host " Watchdog:   MAVIS, IRIS backend and proxy are restarted if they exit."
Write-Host " Press Ctrl+C to stop everything this script started."
Write-Host "================================================================"

try {
    while ($true) {
        Start-Sleep -Seconds 2
        Invoke-Watchdog
    }
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
