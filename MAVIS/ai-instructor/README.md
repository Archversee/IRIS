# AI Flight Instructor

Gentle AI teacher companion for MSFS (and later Prepar3D). Guides a trainee
through Lesson 1, a traffic pattern at Paya Lebar (WSAP) in a C172 G1000,
with live coaching, typed Q&A, and a video avatar.
Everything runs locally: Ollama for the LLM, Kokoro/Piper/SAPI for the voice.

## Quick start (mock mode, no sim needed)

```
cd ai-instructor
pip install -r requirements.txt
python main.py
```

Open `http://<this-pc-ip>:6945` on the tablet (same Wi-Fi). Tap **Start Lesson**.
The mock aircraft flies one circuit by itself so you can see phases, coaching,
and the map end-to-end. Add `?demo=1` to the URL for presenter buttons
(skip phase / night / restart). Time-of-day can also be changed via chat.

## Hardware topology

```
SIM PC (MSFS + Warthog/rudder USB)      LAPTOP RTX 5090 (this app)        TABLET
  MSFS SimConnect  ──── LAN ────▶  Ollama + lesson engine + web  ◀─ browser only
  [optional] hid_forwarder.py ─▶  server + TTS synthesis            :6945
```

- **Sim PC, nothing to install** for telemetry. One config edit only (below).
  Control positions (stick/throttle/rudder as the sim sees them) already arrive
  via SimConnect. *Optionally*, for raw input timing: copy `tools/hid_forwarder.py`
  there, `pip install pygame`, run `python hid_forwarder.py --to <laptop-ip>`,
  and start MAVIS with `python main.py --sim msfs --input udp`.
- **Laptop** runs everything else. A browser on the laptop at `localhost:6945`
  works exactly like the tablet.

### Enable network SimConnect on the sim PC (one-time)
Edit `SimConnect.xml` (MSFS: `%APPDATA%\Microsoft Flight Simulator\SimConnect.xml`;
create if missing) and make sure it has a global IPv4 section:
```xml
<SimConnect.Comm>
  <Protocol>IPv4</Protocol>
  <Scope>global</Scope>
  <Address>0.0.0.0</Address>
  <Port>500</Port>
  <MaxClients>64</MaxClients>
</SimConnect.Comm>
```
Allow port 500 through the sim PC's firewall. Restart MSFS.

## Real sim (run on the laptop)

1. `pip install -r requirements.txt` plus `SimConnect` and ONE voice engine,
   recommended: `pip install kokoro soundfile` (or `pyttsx3` for a basic voice;
   `engine: auto` picks the best installed). Voice plays on the **tablet** by
   default (`voice.output: tablet`).
2. Ollama on the laptop: `ollama pull qwen2.5:14b-instruct`
   (`coach.ollama_url: http://127.0.0.1:11434`).
3. `config.yaml`: set `sim.host` to the sim PC's LAN IP (the app writes
   `SimConnect.cfg` automatically from it).
4. In MSFS: C172 G1000, parked at WSAP runway 02, daytime.
5. `python main.py --sim msfs` (add `--input udp` if using hid_forwarder / joystick PTT)

## Replay a recorded flight (no sim needed)

`python main.py` defaults to `--sim replay`: a logged flight plays back as live
telemetry, so the map, gauges, controls, coaching and score are the real flight
rather than the mock aircraft. To test a new recording, overwrite two files and
start the app - no code or config edits:

| Drop in | What it is |
|---|---|
| `data/recordings/replay/flight.csv` | the logger's SimConnect CSV export |
| `../../frontend/public/media/videos/replay.mp4` | screen recording of the same flight, started and stopped with the logger |

- The runway end is detected from the log's initial heading, and the lesson
  geometry (map, pattern, scoring) follows it.
- The first start after a new `replay.mp4` spends ~30 s building
  `replay.web.mp4` (browser-ready, fixed 30 fps, dropped frames held); later
  starts reuse it. The clip is started the moment playback begins, so it runs
  in step with the log.
- Sanity-check a log without the UI: `python tools/score_log.py data/recordings/replay/flight.csv --reciprocal`
  prints the phase transitions and scorecard (`--reciprocal` when flown from runway 20).
- Recording tips: 30 fps is enough and avoids the encoder dropping frames;
  log `AILERON_POSITION` too, or the stick's roll axis is inferred from bank.
- Mock aircraft: `python main.py --sim mock`. Live sim: `--sim msfs` (below).

### Milestone-1 spike checklist (do this first)
- [ ] Telemetry reads OK (`--sim msfs`, watch log/UI)
- [ ] Day→night works: `curl -X POST localhost:6945/demo/night`
      If the sim ignores ZULU_*_SET events (version-dependent), bind a key in
      MSFS to a time preset as fallback.
- [ ] Verify RWY 02 threshold lat/lon in `lessons/lesson01_pattern.yaml`
      against the sim (slew to threshold, read UI values).

## Layout

```
main.py                 wiring + tick loop (10 Hz)
adapters/  sim_msfs.py  SimConnect (MSFS/P3D), all sim-specific code lives here
           sim_mock.py  self-flying mock for development/demo
           input_hid.py Warthog/rudder raw HID via device profile YAMLs
core/      phase_detector.py  pattern geometry -> phase transitions
           monitors.py        altitude/speed/flaps/bank/centerline/control checks
           lesson_engine.py   state machine + optional night event + cooldowns

See **docs/phase-transitions.md** for exact rules to advance each phase.
           lesson_engine.py   state machine + optional night event + cooldowns
coach/     llm_coach.py  Ollama phrasing + typed Q&A, template fallback
           phrases.py    gentle persona + fallback lines (edit tone here)
voice/     tts.py        kokoro | piper | pyttsx3, all local
web/       server.py + static/index.html   tablet UI (avatar, chat, map, gauges)
profiles/  aircraft & device YAMLs         lessons/  lesson scripts
```

## Swapping things (the flexibility story)

- **Sim → Prepar3D:** new adapter class in `adapters/` (P3D speaks SimConnect;
  mostly variable-name tweaks), set `sim.adapter`.
- **Aircraft:** copy `profiles/aircraft/c172sp_g1000.yaml`, change speeds/flaps/facts.
- **Stick/throttle/rudder:** new YAML in `profiles/devices/` mapping axes.
- **Voice/LLM:** `voice.engine`, `coach.model` in config.
