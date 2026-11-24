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

If it also prints *"no inbound firewall rule for TCP 3000"*, go back to §8.2.

Then:

1. **Sign in** at `http://localhost:3000` as `admin` / `admin` (or your
   `ADMIN_*` values), and change the password under **Profile**.
2. In **Accounts**, create instructor and trainee accounts. Every screen,
   tablets included, signs in with an account.
3. Start MSFS with the C172 G1000 parked at WSAP runway 02, then start a
   lesson from **Live**.

### 9.1 Connecting a tablet

1. Join the **same network** as the sim PC.
2. Open `http://<sim-pc-ip>:3000/live` and sign in.
3. **Tap the screen once** after the page loads. Tablet browsers only allow
   sound after the first touch, so the instructor's voice stays silent until
   then.
4. **Push-to-talk microphone:** browsers only allow the mic on `https://`
   pages or on `localhost`. On a plain `http://<ip>` address the mic button
   reports that the mic is unavailable. To allow it in Chrome on the tablet:
   - Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
   - Add `http://<sim-pc-ip>:3000`, set the flag to *Enabled*, and relaunch
     Chrome.

   Typed chat works without this.

With `voice.output: tablet`, the voice plays in **every** browser that has
the live page open. If only the tablet should speak, close or mute the live
page on the sim PC. Finish & save or Finish & discard on any screen sends
every other screen back to its menu.

### 9.2 First-run checklist

- [ ] `http://localhost:3000/health` shows both backends up.
- [ ] The tablet loads `/live` and can sign in.
- [ ] Live shows telemetry moving when you move the aircraft in MSFS (if
      not, see §6.1).
- [ ] A coaching line plays on the tablet after you tap once.
- [ ] Typing a question in chat gets an LLM answer, not only template lines
      (if not, see §6.2 and §8.3).
- [ ] Day→night works: `curl.exe -X POST http://localhost:6945/demo/night`.
- [ ] *(OBS)* Starting a lesson starts both recordings; ending it stops them.
- [ ] *(Smart Eye)* Live Monitor shows gaze during a flight.

---

## 10. Day-to-day

```powershell
git pull
cd frontend; npm run build; cd ..      # after any pull that touched frontend/
./scripts/launch_all.ps1
```

| Launcher option | Effect |
|---|---|
| `-SkipAdapter` | Don't start the OBS/Smart Eye adapter this session. |
| `-SkipMsfsCheck` | Skip the "is MSFS running" warning (mock/replay testing). |
| `-LocalOnly` | Proxy listens on this PC only; tablets can't connect. |
| `-Port 8080` | Serve the website on a different port. Update the firewall rule to match. |
| `-DryRun` | Print what would run, start nothing. |

If a `requirements.txt` changed, rerun that component's `pip install -r
requirements.txt`. If a `db/*.sql` file was added, apply it as in §3.

### Troubleshooting

| Symptom | Check |
|---|---|
| Tablet can't open the page | Same network (not guest Wi-Fi)? Network set to Private (§8.1)? Rule for TCP 3000 (§8.2)? A python Block rule (§8.5)? Launcher not run with `-LocalOnly`? |
| Page loads but shows "MAVIS/backend unavailable" | `logs/launcher/*.err.log` on the sim PC. |
| Tablet is silent | Tap once after loading. Is `voice.output` set to `tablet`? Is the device's media volume up? |
| Mic button says the mic is unavailable | The HTTPS rule in §9.1, step 4. |
| Only template coaching, no LLM answers | `curl.exe http://<llm-pc-ip>:11434/api/tags` from the sim PC; `OLLAMA_HOST` and firewall on the LLM PC (§6.2, §8.3). |
| MAVIS keeps retrying SimConnect | Is MSFS running with a flight loaded? `SimConnect.xml` has the IPv4/500 section and MSFS was restarted (§6.1). |
| No gaze in Live Monitor | SEP destination IP and port, UDP 5001 rule, time sync on both PCs (§7.2). |
| Old UI after `git pull` | `npm run build` in `frontend/`, then reload the page. |

---

## 11. Repo layout

```
backend/                 IRIS backend (FastAPI): app/main.py, app/routers/*, app/config.py
frontend/                React app: src/pages (IRIS), src/live (MAVIS live coaching UI), src/sop
proxy/app.py             the single website origin: / + /api + /mavis (+ WebSockets)
MAVIS/ai-instructor/     the live instructor (see §12)
Sim_Data_Adapter/        LiveAdapter.py: OBS recording control + Smart Eye live bridge
db/                      schema.sql + additive migrations
scripts/launch_all.ps1   starts the whole stack with health checks and a watchdog
docs/                    INTEGRATION_PLAN.md (architecture), LIVE_REBUILD_PLAN.md, ...
data/videos/             OBS recordings (Instruments/, OTW/)
```

`docs/INTEGRATION_PLAN.md` describes how MAVIS and IRIS fit together.

---

## 12. MAVIS reference

MAVIS is a gentle instructor for MSFS. It guides a trainee through a traffic
pattern at Paya Lebar (WSAP) in a C172 G1000, with live coaching, typed and
spoken Q&A, and a video avatar. Everything runs locally: Ollama for the LLM,
and Kokoro, Piper or SAPI for the voice. Paths in this section are relative
to `MAVIS/ai-instructor/`.

### 12.1 Running MAVIS on its own

```powershell
.venv\Scripts\python main.py                 # sim.adapter from config.yaml
.venv\Scripts\python main.py --sim mock      # self-flying mock aircraft, no sim needed
.venv\Scripts\python main.py --sim replay    # a recorded flight played back as live
.venv\Scripts\python main.py --sim msfs --input udp   # with hid_forwarder.py (§12.3)
```

MAVIS also serves its own older standalone page at `http://localhost:6945`.
Add `?demo=1` to it for presenter buttons (skip phase, night, restart). The
IRIS website's **Live** page is the main UI.

### 12.2 Testing without a sim

- **Mock:** `--sim mock` flies one circuit by itself, so you can see phases,
  coaching and the map end to end.
- **Replay:** `--sim replay` plays a logged flight back as live telemetry. To
  test a new recording, overwrite two files (no code or config edits):

  | Drop in | What it is |
  |---|---|
  | `data/recordings/replay/flight.csv` | The logger's SimConnect CSV export |
  | `frontend/public/media/videos/replay.mp4` (from the repo root) | A screen recording of the same flight |

  The runway end is detected from the initial heading. The first start after
  a new `replay.mp4` spends about 30 s building `replay.web.mp4`. To check a
  log without the UI, run
  `python tools/score_log.py data/recordings/replay/flight.csv --reciprocal`
  (`--reciprocal` when flown from runway 20). When recording, 30 fps is
  enough; also log `AILERON_POSITION`.

### 12.3 Split setup: MAVIS on a laptop, MSFS on the sim PC

- **MSFS:** in `SimConnect.xml` on the sim PC, use
  `<Scope>global</Scope>` and `<Address>0.0.0.0</Address>`. Allow TCP 500
  in (§8.2) and restart MSFS.
- **MAVIS:** on the laptop, set `sim.host` in `config.yaml` to the sim PC's
  IP.
- **Proxy and backend:** run them on the laptop too. Tablets then use
  `http://<laptop-ip>:3000`, and the TCP 3000 rule goes on the laptop.
- **Raw stick/throttle timing (optional):**
  1. Copy `tools/hid_forwarder.py` to the sim PC and `pip install pygame`.
  2. Run `python hid_forwarder.py --to <laptop-ip>`.
  3. Start MAVIS with `--input udp`.
  4. Allow UDP 6947 in on the laptop.

### 12.4 Code layout

```
main.py                 wiring: builds the runtime, web actions, the 10 Hz tick loop, server
runtime/   context.py           `rt`: the shared runtime context every module reads
           bootstrap.py         config/lesson loading, sim adapter / engine / scorer factories
           session.py           configure / start / restart / relaunch / quit / close, lesson select
           flight_flow.py       briefing, freeze + control lock, demo, airborne start, night
           tick_steps.py        the tick loop's steps (event routing, scoring, state, track)
           checkpoints.py       "let me try that again": checkpoints, retry, forced repeats
           chat_router.py       chat intents: deterministic rules -> LLM tool pick -> Q&A
           scoring_flow.py      scorecard, debrief (with fallback), leaderboard
           speech.py            serialized TTS channel, reading-lesson narration
           sop_live.py          SOP rules checked live during the flight
           instructor_admin.py  instructor PIN / accounts / reference runs
adapters/  sim_msfs.py  SimConnect (MSFS/P3D), all sim-specific code lives here
           sim_mock.py  self-flying mock for development/demo
           input_hid.py Warthog/rudder raw HID via device profile YAMLs
core/      phase_detector.py  pattern geometry -> phase transitions
           monitors.py        altitude/speed/flaps/bank/centerline/control checks
           lesson_engine.py   state machine + optional night event + cooldowns
coach/     llm_coach.py  Ollama phrasing + typed Q&A, template fallback
           phrases.py    gentle persona + fallback lines (edit tone here)
voice/     tts.py        kokoro | piper | pyttsx3, all local
web/       server.py + static/index.html   HTTP/WS API + the standalone page
profiles/  aircraft & device YAMLs         lessons/  lesson scripts
```

`docs/phase-transitions.md` has the exact rules for advancing each phase.

**How main.py and runtime/ fit together.** `main.py` creates the shared
objects (state, session, coach, event log, and so on), publishes each on
`rt`, and calls one `build_X(rt)` per runtime module. Each module defines its
actions and publishes them on `rt` too (`rt.start`, `rt.finish_lesson`,
`rt.speak`, ...). Code always reads through `rt` at call time and never keeps
its own copy: `select_lesson()` swaps `rt.lesson`, `rt.engine` and
`rt.scorer` mid-run, so a held reference would go stale. A new shared name
needs a slot in `runtime/context.py`.

**The tick loop** (`ticker()` in main.py) is one synchronous, ordered loop by
design: read telemetry, spike filter, `rt.engine.tick()` (phase detection
and monitors), then the `tick_steps` calls in a fixed order. The steps never
`await`; background work is only started from them with
`asyncio.create_task`. Keep deterministic reflexes in this loop; don't move
them into callbacks or async handlers.

### 12.5 Swapping things

- **Sim → Prepar3D:** add an adapter class in `adapters/` (P3D speaks
  SimConnect; mostly variable-name tweaks) and set `sim.adapter`.
- **Aircraft:** copy `profiles/aircraft/c172sp_g1000.yaml` and change the
  speeds, flaps and facts.
- **Stick/throttle/rudder:** add a YAML in `profiles/devices/` mapping the
  axes.
- **Voice/LLM:** `voice.engine` and `coach.model` in `config.yaml`.

---

## 13. Data flow

1. **Fly in MAVIS** (Basic Flight or Free Flight). Each start mints a fresh
   session id that everything else joins on. MAVIS coaches live and scores
   the run. MAVIS is the only thing that starts or ends a session; IRIS only
   views.
2. **IRIS's backend** (`app/mavis_bridge.py`) watches MAVIS's WebSocket feed
   and writes flight telemetry, coaching events, the scorecard and
   leaderboard updates into Postgres as they happen. The first time it sees
   a session, it auto-launches the OBS/Smart Eye adapter (if it isn't already
   running; all ways of starting it are safe to combine).
3. **OBS** records the instrument and out-the-window views automatically.
   Smart Eye's live feed is stored in Postgres as it streams. Smart Eye's
   file export, imported via **Upload** after the flight, replaces it with
   the full-resolution data.
4. **Finish & save** on the scorecard opens the **Save Session** page for
   that session: name, pilot, aircraft, notes and video links. **Finish &
   discard** deletes it.
5. **Review** plays flight and gaze back on one synchronised timeline, phase
   by phase, with MAVIS's coaching callouts alongside.
6. **Analytics** shows derived metrics and gaze dwell per area of interest.
   **Leaderboard** shows MAVIS's scored runs across sessions.

### Live Monitor (view only)

Live Monitor never starts or stops anything. It lists the MAVIS sessions in
progress (`GET /sessions/running`) and lets you pick one to watch. A session
drops off the list as soon as it's ended; "Fly again" starts a new one.

### OBS recording details

`LiveAdapter.py` starts recording on both OBS instances when MAVIS starts a
session, and stops when the session ends (it polls IRIS for `ended_at`). It
keeps running across many flights. Each start and stop is logged as an
`obs_record_start:<name>` or `obs_record_stop:<name>:<path>` event. Match the
files to a session from Save Session or Upload
(`PUT /sessions/{id}/video/{screen}`, where `screen` is `instrument` or
`otw`). The two `start_record()` calls go out one after the other, so the
files are a few ms apart: fine for this app's timestamp sync, but not
frame-accurate.

### CSV column expectations (manual Upload)

- **Flight:** headers matching `db.FLIGHT_COLUMNS` (`backend/app/db.py`) plus
  `timestamp_utc`, which is mapped to `ts`. Only for manually exported CSVs;
  MAVIS sessions write flight data directly.
- **Eye:** a `ts` (ISO-8601) column plus any of `EYE_COLUMNS`
  (`backend/app/db.py`). The `aoi` column drives the dwell analytics.

### Real-time streaming API

`backend/app/routers/stream.py` also accepts live samples:
`POST /sessions/{id}/stream/flight` (one JSON sample) and
`WS /sessions/{id}/stream/ws` (a stream). The backend's API docs are at
`http://localhost:8000/docs` on the sim PC.

---

## 14. Development without the launcher

```powershell
# backend, with auto-reload
cd backend;  .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# frontend dev server with hot reload: proxies /api and /mavis like the real proxy
cd frontend; npm run dev                         # http://localhost:5173 (add -- --host for LAN)

# proxy by hand (add --host 0.0.0.0 so tablets can reach it)
cd proxy;    .venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 3000
```
