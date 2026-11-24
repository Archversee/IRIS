# IRIS + MAVIS

**IRIS** (*Integrated Review and Insight System for Eye Tracking Data within
Flight Simulators*) records, reviews and analyses simulator flights alongside
eye-tracking data. **MAVIS** (`MAVIS/ai-instructor/`) is the live AI flight
instructor that coaches the trainee in MSFS. Both are served together as one
website that you open on the sim PC and on any number of tablets.

This README is a first-time setup guide. Follow sections 1–9 in order on a
fresh machine. Section 10 covers day-to-day running, and everything from
section 11 on is reference.

| Part | What it is | Port |
|---|---|---|
| Proxy (`proxy/`) | The website: serves the React app and forwards `/api` and `/mavis` | **3000** (the only port tablets use) |
| IRIS backend (`backend/`) | FastAPI + asyncpg: sessions, review, analytics, accounts | 8000 (this PC only) |
| MAVIS (`MAVIS/ai-instructor/`) | Live instructor: SimConnect, lesson engine, coaching, voice | 6945 (reached through the proxy) |
| Frontend (`frontend/`) | React (Vite), built into `frontend/dist` and served by the proxy | none (built files) |
| PostgreSQL 17 | The database both backends use | 5432 (this PC only) |
| OBS/Smart Eye adapter (`Sim_Data_Adapter/`) | Optional: starts/stops OBS recording, receives Smart Eye's live gaze | UDP 5001 in |
| Ollama | Local LLM for coaching phrasing, SOP parsing and debriefs (usually on a separate GPU PC) | 11434 on that PC |

---

## 1. How the machines fit together

```
                         ┌──────────────── SIM PC (e.g. 192.168.0.103) ─────────────────┐
  TABLETS (browser) ───▶ │ :3000 proxy ─┬─▶ /       frontend/dist                         │
  http://<sim-pc>:3000   │              ├─▶ /api    IRIS backend :8000 ─▶ Postgres :5432  │
                         │              └─▶ /mavis  MAVIS :6945 ─▶ MSFS (SimConnect :500) │
                         │  OBS ×2 (:4455, :4456) ◀── adapter ◀── UDP :5001 ◀──┐          │
                         └─────────────────────────────────────────────────────┼──────────┘
                                │ coaching / SOP / debrief                      │
                                ▼                                               │
                   LLM PC (e.g. 192.168.0.100)                SMART EYE PC ─────┘
                   Ollama :11434                              (SEP live gaze over UDP)
```

- Everything except Ollama and Smart Eye runs on **one sim PC**. A browser on
  the sim PC at `http://localhost:3000` works exactly like a tablet.
- Tablets only need a browser on the same network. They connect to port 3000
  on the sim PC, and nothing else.
- Several tablets (and the sim PC's own browser) can be open at once. They all
  show the same live session, and any of them can drive it.
- Give the sim PC and the LLM PC fixed addresses (a DHCP reservation in the
  router), so the URLs and `config.yaml` don't change between sessions.

Running MAVIS on a separate laptop instead of the sim PC also works; see
§12.3.

---

## 2. Install the prerequisites (sim PC)

| Software | Notes |
|---|---|
| **Git** | To clone the repo. |
| **Python 3.12** (3.12.10 is the tested version) | Install from python.org with the **`py` launcher** ticked. Each component gets its own venv. |
| **Node.js 20 LTS or newer** | Only to build the frontend. |
| **PostgreSQL 17** | See §3. |
| **Microsoft Flight Simulator** | With the C172 G1000. |
| **OBS Studio** *(optional)* | For the instrument and out-the-window video recordings (§7). |
| **Tesseract OCR** *(optional)* | Only for uploading scanned SOP PDFs. Use the UB Mannheim Windows build. |

Then clone the repo:

```powershell
git clone <repo-url> IRIS
cd IRIS
```

All commands below are run from the repo root unless they say otherwise.

**PowerShell scripts:** Windows blocks `.ps1` files by default. Allow them once
for your user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 3. Database (PostgreSQL 17)

1. **Install.** Download the EDB installer for **version 17** from
   postgresql.org. Keep the defaults (port **5432**). When it asks for the
   `postgres` superuser password, **use `postgres`**: that matches the
   built-in default connection string, so no `.env` is needed. If you use a
   different password, see step 3.
2. **Put `psql` on PATH.** Keep "Command Line Tools" ticked. If the
   installer doesn't add `C:\Program Files\PostgreSQL\17\bin` to PATH, add
   it yourself (System Properties → Environment Variables) and open a new
   terminal. Check that it's running:
   ```powershell
   pg_isready        # .../postgres:5432 - accepting connections
   ```
3. **Apply the schema.** All of these files are idempotent, so they're safe to
   re-run. The backend also applies the accounts and later tables itself at
   startup.
   ```powershell
   $env:PGPASSWORD = "postgres"
   psql -U postgres -d postgres -f db/schema.sql
   psql -U postgres -d postgres -f db/schema_mavis_integration.sql
   psql -U postgres -d postgres -f db/schema_sop_rules.sql
   psql -U postgres -d postgres -f db/schema_rule_results.sql
   psql -U postgres -d postgres -f db/schema_auth.sql
   ```
4. **Backend settings (optional).** The backend reads `backend/.env` if it
   exists. Create it only to change a default:
   ```ini
   DATABASE_URL=postgresql://postgres:<your-password>@localhost:5432/postgres
   ADMIN_USERNAME=admin          # first admin account, created on first start
   ADMIN_PASSWORD=admin          # change it from Profile after signing in
   ```
   The other settings (video folder, Live Monitor window titles, and so on)
   are listed in `backend/app/config.py`. Keep `PUBLIC_BASE_URL` at its
   default `/api`: an absolute `http://localhost:...` URL breaks video
   playback on tablets.

---

## 4. Install the software

Each Python component has its own venv, named `.venv` inside its folder.
`scripts/launch_all.ps1` expects exactly these paths.

### 4.1 IRIS backend

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..
```

### 4.2 Frontend (build it)

```powershell
cd frontend
npm install
npm run build          # creates frontend/dist, which the proxy serves
cd ..
```

**Run `npm run build` again after every `git pull`.** The proxy serves the
built files, so frontend changes don't appear until you rebuild.

### 4.3 MAVIS

```powershell
cd MAVIS\ai-instructor
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install pysimconnect pygame kokoro soundfile sounddevice pyttsx3
cd ..\..
```

The second install line is the Windows/sim-PC-only part:

- `pysimconnect`: MSFS telemetry.
- `pygame`: local joystick/HID.
- `kokoro`, `soundfile`, `sounddevice`: the preferred voice.
- `pyttsx3`: a basic Windows voice used as a fallback.

The first start downloads the Kokoro voice, the Whisper speech-to-text model
and the speaker-ID model, so it needs internet once.

### 4.4 Proxy

```powershell
cd proxy
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..
```

### 4.5 OBS/Smart Eye adapter (optional)

```powershell
cd Sim_Data_Adapter
py -3.12 -m venv .venv
.venv\Scripts\pip install obsws-python websocket-client
cd ..
```

---

## 5. Configure MAVIS (`MAVIS/ai-instructor/config.yaml`)

| Key | Set it to |
|---|---|
| `sim.adapter` | `msfs` for the real sim (`mock` or `replay` for testing without one, see §12.2) |
| `sim.host` | `127.0.0.1` when MAVIS runs on the sim PC (the normal case) |
| `coach.ollama_url` | `http://<llm-pc-ip>:11434`, or `http://localhost:11434` if Ollama is on this PC |
| `sop.ollama_url`, `debrief.ollama_url` | Same Ollama as above |
| `coach.model`, `sop.model`, `debrief.model` | Models you have pulled in Ollama (§6.2) |
| `voice.output` | `tablet` (the voice plays in every browser on the live page) or `sim_pc` (PC speakers only) |
| `instructor.pin` | The PIN for the first instructor account seeded on first run |
| `sop.tesseract_cmd` | `C:/Program Files/Tesseract-OCR/tesseract.exe` if Tesseract isn't on PATH |

MAVIS writes `SimConnect.cfg` itself from `sim.host` and `sim.port`, so don't
edit that file by hand.

---

## 6. MSFS and Ollama

### 6.1 Enable SimConnect over IPv4 in MSFS (sim PC)

MAVIS connects to MSFS over IPv4 on port 500. Edit `SimConnect.xml`, which
lives at:

- MSFS 2020, Steam: `%APPDATA%\Microsoft Flight Simulator\SimConnect.xml`
- MSFS 2020, Microsoft Store:
  `%LOCALAPPDATA%\Packages\Microsoft.FlightSimulator_8wekyb3d8bbwe\LocalCache\SimConnect.xml`

Create the file if it's missing. Make sure it has an IPv4 section on port 500:

```xml
<SimConnect.Comm>
  <Protocol>IPv4</Protocol>
  <Scope>local</Scope>          <!-- use global only if MAVIS runs on another PC (§12.3) -->
  <Address>127.0.0.1</Address>  <!-- 0.0.0.0 with global scope -->
  <Port>500</Port>
  <MaxClients>64</MaxClients>
</SimConnect.Comm>
```

Restart MSFS after editing the file. With `local` scope, no firewall rule is
needed.

### 6.2 Ollama (LLM PC, or the sim PC)

1. Install Ollama, then pull the models named in `config.yaml`:
   ```powershell
   ollama pull qwen2.5:7b-instruct      # coach (fast, short replies)
   ollama pull qwen3.6:27b              # SOP parsing + debriefs (or a smaller model; update config.yaml)
   ```
2. **If Ollama is on a different PC from MAVIS**, make it listen on the
   network. By default it only answers `localhost`. On the LLM PC:
   ```powershell
   [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
   ```
   Then quit Ollama from the system tray and start it again, and add the
   firewall rule in §8.3.
3. From the sim PC, check that it answers:
   ```powershell
   curl.exe http://<llm-pc-ip>:11434/api/tags
   ```

If Ollama can't be reached, MAVIS still coaches using its built-in template
lines. Typed Q&A, SOP upload and debriefs need the LLM.

---

## 7. OBS and Smart Eye (optional)

### 7.1 OBS: two instances

1. Run two OBS Studio instances, one for the **instrument** view and one for
   the **out-the-window** view. Start the second one with
   `obs64.exe --multi` to skip the "already running" warning.
2. For the **instrument** instance, set the Base (Canvas) and Output (Scaled)
   resolution to exactly **1920×1080**, matching Smart Eye's calibrated
   screen. Gaze coordinates are in that screen's pixels, so any mismatch
   misaligns every gaze-to-instrument mapping.
3. In each instance, go to **Tools → WebSocket Server Settings**:
   - Enable the server.
   - Instrument instance: port **4456**. Out-the-window instance: port **4455**.
   - Leave authentication **unticked**. The adapter connects without a
     password.
4. Point both recording paths at `data/videos/Instruments` and
   `data/videos/OTW` (or wherever `video_dir` in `backend/app/config.py`
   points).
5. If your hosts or ports differ, edit `OBS_INSTANCES` in
   `Sim_Data_Adapter/LiveAdapter.py`.

OBS listens on the sim PC only, so no firewall rule is needed.

### 7.2 Smart Eye Pro (on the Smart Eye PC)

1. In SEP's Communication Settings, set the UDP destination to
   `<sim-pc-ip>`, port **5001** (`UDP_LISTEN_PORT` in `LiveAdapter.py`). The
   network output must include the fields listed in `FIELD_SELECTION` in
   that file.
2. Turn on automatic time sync on **both** PCs (Settings → Time & Language →
   Date & time → *Set time automatically*). SEP stamps each sample with real
   UTC time.
3. On the sim PC, allow UDP 5001 in (§8.2).

The live feed is a best-effort preview. Smart Eye's own file export, imported
from **Upload**, is still the full-resolution record.

---

## 8. Network and firewall

Do this once on each PC. All commands go in an **administrator** PowerShell.

### 8.1 Set the network to Private

Windows applies "Public" network rules on networks it doesn't trust, and the
rules below are for the **Private** profile only.

```powershell
Get-NetConnectionProfile                                    # note the InterfaceAlias and NetworkCategory
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private   # use your alias
```

Do this on the sim PC, the LLM PC and the Smart Eye PC. Use a normal network,
not **guest Wi-Fi**: guest networks often isolate devices from each other, so
the tablet can't reach the sim PC even with every rule in place. A quick
test: if `ping <sim-pc-ip>` from another PC fails, the network itself is
blocking traffic.

### 8.2 Sim PC rules

```powershell
# Tablets -> the website. Always needed.
New-NetFirewallRule -DisplayName "MAVIS-IRIS 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private

# Smart Eye PC -> live gaze. Only if SEP runs on another PC.
New-NetFirewallRule -DisplayName "MAVIS-IRIS Smart Eye 5001" -Direction Inbound -Protocol UDP -LocalPort 5001 -Action Allow -Profile Private
```

These two are only for the split setup (§12.3):

```powershell
# MAVIS on another PC -> MSFS SimConnect
New-NetFirewallRule -DisplayName "MSFS SimConnect 500" -Direction Inbound -Protocol TCP -LocalPort 500 -Action Allow -Profile Private
```

The HID forwarder rule (UDP 6947) goes on the PC running MAVIS, not the sim PC:

```powershell
# hid_forwarder.py on the sim PC -> MAVIS
New-NetFirewallRule -DisplayName "MAVIS HID forwarder 6947" -Direction Inbound -Protocol UDP -LocalPort 6947 -Action Allow -Profile Private
```

### 8.3 LLM PC rule (only if Ollama is on its own PC)

```powershell
New-NetFirewallRule -DisplayName "Ollama 11434" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private
```

### 8.4 Ports that need no rule

| Port | Why |
|---|---|
| 5432 Postgres, 8000 IRIS backend | Used only on the sim PC. |
| 4455/4456 OBS | Used only on the sim PC. |
| 6945 MAVIS | Tablets reach it through the proxy at `/mavis`. MAVIS itself listens on all interfaces, so Windows may ask about it on first start. Either answer is fine; opening 6945 only exposes MAVIS's old standalone page. |

### 8.5 The "Allow access?" pop-up trap

The first time a Python service listens on the network, Windows asks whether
to allow it. Clicking **Cancel** quietly creates an inbound **Block** rule for
`python.exe`, and a block rule overrides every allow rule above. If the
tablet can't connect although the rules exist, look for one:

```powershell
Get-NetFirewallApplicationFilter | Where-Object Program -like "*python*" |
  Get-NetFirewallRule | Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" } |
  Select-Object DisplayName, Profile, Enabled
```

Remove any you find, by piping the same command into
`Remove-NetFirewallRule`, or delete them in *Windows Defender Firewall with
Advanced Security → Inbound Rules*.

### 8.6 Check it from another PC

```powershell
Test-NetConnection <sim-pc-ip> -Port 3000      # TcpTestSucceeded : True
Test-NetConnection <llm-pc-ip> -Port 11434     # if Ollama is separate
```

Or open `http://<sim-pc-ip>:3000/health` in the tablet's browser. It reports
whether both backends are up. UDP 5001 can't be tested this way; watch for
gaze in Live Monitor instead.

---

## 9. First start

```powershell
./scripts/launch_all.ps1
```

The launcher starts, in order: Postgres (if it's a stopped Windows service),
an advisory MSFS check, MAVIS, the IRIS backend, the OBS/Smart Eye adapter,
and the proxy. Each step is health-checked, but a failure doesn't stop the
script; the website shows banners for whatever is down. A watchdog restarts
MAVIS, the backend or the proxy if one exits. Logs go to `logs/launcher/`,
and **Ctrl+C** stops everything it started.

When it's done, it prints:

```
 Dashboard:  http://localhost:3000
 Tablets:    http://192.168.0.103:3000/live
```
