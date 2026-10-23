# MAVIS — AI Flight Instructor
## Design & Handover Document v2.0

**Status:** working showcase, verified against real MSFS 2020 over LAN.
**Audience of this doc:** the next engineer/model (Opus) extending the system. It describes what exists, what was learned, and where to take it — especially the path from scripted coach to agentic instructor.

---

## 1. What this is

MAVIS is a gentle AI flight instructor that runs *beside* a flight simulator. A complete beginner sits at the sim (MSFS 2020, Cessna 172 G1000, Paya Lebar Air Base WSAP); MAVIS watches live telemetry and their control inputs, talks them through Lesson 1 — one circuit of the traffic pattern (take off, four turns, full-stop landing) — answers typed questions mid-flight, can change the sim time of day on request, and can teleport the trainee back to a checkpoint to retry a phase. Everything runs locally: no cloud calls anywhere.

The persona is deliberate and non-negotiable: warm, patient, never military. Corrections are invitations ("let's", "whenever you're ready"), the word "wrong" is banned, one instruction at a time, plain language for laymen (no "abeam", "V/S", or "flaps 20" — it's "halfway along this side", "climb rate", "put the flaps down").

## 2. Hardware topology (as deployed)

```
SIM PC (MSFS 2020 + Warthog stick/throttle + Virpil pedals)
   │  SimConnect over TCP :500  (SimConnect.xml edited; nothing installed)
   │  [optional] tools/hid_forwarder.py → UDP :6947 (raw control timing)
   ▼
LAPTOP RTX 5090 — runs everything: this app + Ollama (LLM) + Kokoro (TTS)
   │  FastAPI + WebSocket :6945
   ▼
TABLET (browser only) — the trainee UI; voice plays here
```

A browser on the laptop at `localhost:6945` is equivalent to the tablet.

## 3. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11, asyncio, single process | venv at `ai-instructor/.venv` |
| Sim link | Python-SimConnect | remote via auto-written `SimConnect.cfg` (written to python.exe dir + Documents — the DLL does NOT read cwd) |
| LLM | Ollama, `qwen2.5:14b-instruct` | configurable endpoint; every LLM use has a deterministic fallback |
| TTS | Kokoro (local, GPU) → Piper → pyttsx3, auto-selected | synthesized to wav on laptop, **played by the tablet browser**; ~60 s first-load, so it initializes in background after the web server is up |
| Web | FastAPI + WebSocket (5 Hz state push), single-file vanilla-JS UI, canvas map (no external JS libs — must work offline) | |
| Raw input | pygame (USB HID) via YAML device profiles; UDP forwarder for remote devices | standard HID = any stick works; profiles only map axis numbers |
| Config | YAML everywhere: `config.yaml`, `profiles/aircraft/*.yaml`, `profiles/devices/*.yaml`, `lessons/*.yaml` | |

## 4. How to run

```powershell
cd C:\Users\ares\Desktop\FION\ai-instructor
.venv\Scripts\activate
python main.py              # mock mode: self-flying demo aircraft, no sim needed
python main.py --sim msfs   # real sim (sim.host in config.yaml = sim PC's LAN IP)
```
Open `http://<laptop-ip>:6945` on the tablet. `?demo=1` adds presenter buttons (skip phase / night / restart). Full setup details (SimConnect.xml, firewall, packages) are in the repo-root `README.md`, §4 MAVIS.

**Mock mode matters:** it's a kinematic C172 that flies the whole lesson, obeys MAVIS's callouts (`comply()`), makes one deliberate mistake (height overshoot) and corrects it after her nudge, rotates exactly at the called 55 kt, and full-stops after the landing. Nearly everything was developed and demo-rehearsed against it. There is also an accelerated headless test pattern (tick the mock with fake dt) used throughout development — keep using it.

**Single-circuit audit fixes (Jul 2026), preserve these:** `base_turn_past_abeam_m` is **1000 m** — 500 m made the final leg an unflyable ~18° dive (the mock arrived at 27 kt; a trainee following the drawn line couldn't do better); 1000 m gives a real ~600–700 fpm final. The mock's descent starts **abeam the threshold** (like a real circuit) and it touches down ~55 kt with a proper rollout. The demo expert JSONs (`profiles/experts/`) must be **regenerated whenever pattern geometry changes** — the seeder never overwrites, so stale tracks linger silently (delete the `demo: true` files or rewrite their `track`). Also fixed then: the mock's mid-field diagonal waypoint that zeroed the track score — verified end-to-end at 80/B (track 62, speed 95, altitude 92, landing 74, smoothness 100).

## 5. What is implemented (v1)

**Lesson engine:** phase state machine (BRIEFING → PRE_TAKEOFF → TAKEOFF_ROLL → CLIMB_OUT → CROSSWIND → DOWNWIND → BASE → FINAL → FLARE_TOUCHDOWN → ROLLOUT → DEBRIEF) for one circuit, driven by pattern geometry (along/cross track, heading offset). `total_circuits: 1` in the lesson YAML; touch-and-go and multi-circuit logic remain in the detector for future lessons. Transitions are **self-healing**: every phase has a position-based fallback so a missed detection window cannot strand the tracker a lap behind (this bug happened; the fix is load-bearing). 30-run jittered-timing torture test passes.

**Coaching:** continuous monitors (speed, altitude, bank, flaps, centerline, sink rate, rotation timing, control smoothness) emit severity-tagged events with per-code cooldowns, an 8 s settle-in grace period after each phase change, and a priority queue into the Coach. The Coach phrases events via the local LLM (persona prompt + fallback template per event, verbatim for rehearsed set pieces), with three latency defenses learned the hard way: corrections jump the queue (2.5 s gap vs 8 s), a speech lock serializes all voice (overlap bug), and **stale-callout suppression** — at speaking time, if live telemetry shows the problem already fixed, the line is dropped rather than contradicting what the trainee sees.

**Chat (always available):** typed Q&A through the LLM with aircraft facts; deterministic intent routing *before* the LLM for things that must be reliable: open/close controls guide, **time-of-day changes** (three tiers: explicit clock parse "12:45pm"/"1400 hrs" → named times "dusk" → LLM JSON extraction for fuzzy phrasing), and **checkpoint retry** ("let me try the landing again" → teleport). Weather requests get a graceful "coming soon".

**Environment control:** verified working on real MSFS 2020 — ZULU time-set events flip day/night live when the trainee asks (chat intent) or via the `?demo=1` presenter button. L1 no longer includes a scripted day→night moment (reserved for a future night-circuit lesson). UI shows actual sim clock (`☀️ 1000 HRS` / `🌙 2030 HRS`).

**Checkpoints/teleport:** auto-snapshot at every phase entry. Mock: perfect state restore. MSFS: settable sim vars (lat/lon/alt/hdg/velocity) — *experimental, not yet verified at the sim*. Track history is segmented per attempt: old attempts render dim/dashed, current attempt bright green.

**UI (tablet):** avatar (intro.mp4 speaks the greeting with its own audio — the INTRO text appears in chat un-TTS'd; idle.mp4 loops muted; speaking glow + waveform; minimize/mute), chat panel, canvas map (runway, ideal pattern, segmented flown track, zoom + auto-expand follow), lesson strip with legend and "Circuit N of 1", layman gauges (SPEED knots / HEIGHT feet / CLIMB ▲▼ with green target bands, scales from aircraft profile), Controls panel (stick dot, power/rudder/flaps bars — flaps bar reflects the binary up/down hardware switch), floating draggable/maximizable trainee-station video monitor (MJPEG/video URL sources from config; URLs not yet wired to a real stream), ⓘ Controls Guide full-screen image (drop-in replaceable `web/static/cockpit.png`), theme fully driven by `config.yaml` (`accent: blue` → one-word rebrand).

**Flexibility seams (all exercised):** `SimAdapter` (mock/MSFS; P3D is a variable-name variant), `AircraftProfile` YAML (speeds, flaps *and* `control_style: switch|notches` which changes MAVIS's phrasing), device profile YAMLs, `VoiceOutput` chain, theme config.

## 6. Verified vs pending on real hardware

| Item | Status |
|---|---|
| SimConnect over LAN, telemetry | ✅ verified |
| Day→night via ZULU events (MSFS 2020) | ✅ verified live |
| Kokoro voice → tablet playback | ✅ verified |
| Vertical speed | ✅ fixed (sim var units were garbage; now self-computed from altitude, smoothed) |
| Stick + throttle via sim control vars | ✅ verified |
| Rudder read | ⚠ pending (user fixing axis mapping) |
| Teleport on MSFS | ⚠ experimental — next at-sim test |
| Raw HID forwarder (input timing) | ⚠ written, not yet field-tested |
| Weather control | ❌ **Confirmed** — MSFS 2020's SimConnect weather API is deprecated & non-functional (new meteoblue engine); there is no SimConnect call to set weather or select a preset, and ambient-weather sim-vars are read-only. Not live-changeable. Options: pick a preset pre-flight, ship custom `.WPR` presets (need a sim restart to change), or a third-party engine (REX / Active Sky). See §13.1. |

Known gotchas encoded in the code: SimConnect.cfg search path; position spike glitches (one-sample teleports) are filtered from both track and lesson logic; avatar videos must be 3:4 content (a pillarboxed idle.mp4 was auto-cropped once with ffmpeg).

## 7. Is this agentic? Honest answer: not yet.

Today MAVIS is a **reactive scripted system with LLM dressing**: a deterministic state machine decides *when/what*, the LLM decides *how to say it*, plus a handful of chat-triggered tools (time, teleport, guide). She never *decides* anything — she follows the circuit script and comments on deviations from it. If the trainee flies off toward the sea, she'll emit a centerline nudge and then go silent, because the script has no opinion about "nowhere near the pattern."

### 7.1 The agentic architecture to build

Move to a **perceive → assess → decide → act** loop where the LLM (or a policy layer) owns decisions and calls tools:

```
Perception (exists): telemetry, geometry, control inputs, phase, history ring buffer
      ▼
Assessment (build): per-phase performance scoring — was that downwind within
  tolerance? trend analysis (diverging vs converging), deviation classification
  (minor drift / off-profile / lost / unsafe)
      ▼
Decision (build): an instructor policy loop, running ~1 Hz on the local LLM with
  a constrained tool schema + the deterministic monitors as its "reflexes".
  Modes (below) act as policy constraints on what it may decide.
      ▼
Tools (mostly exist): say(text, priority) · set_time(h,m) · teleport(checkpoint)
  · repeat_phase(phase) · insert_exercise(id) · open_guide() · pause_lesson()
  · mark_phase_score(phase, grade, notes) · end_lesson_with_debrief()
```

Design rule carried over from v1, keep it: **deterministic reflexes, agentic judgment.** Safety-critical and precision behaviors (rotate callout, stale suppression, clock parsing) stay rule-based; the agent decides pedagogy — whether to let a mistake slide, drill it, or rewind. Every agent decision must degrade gracefully if the LLM is slow: the v1 state machine remains as the fallback spine.

### 7.2 Instructor modes (user requirement)

Mode is a **policy configuration** (see `profiles/modes.yaml` + `core/modes.py`), not a fork of the code. Four menu modes:

| Mode | Audience | Coaching | Forced repeats | Chat |
|------|----------|----------|----------------|------|
| **Visitor** | novices / airshow visitors | turn-by-turn + direct nudges, layman default | no | on |
| **Trainee** | prepared student pilots | quiet (`reactive`): clearance + safety only; no PHASE/TURN/nudge guidance | yes | on |
| **Pilot** | serious practice | same quiet filter as Trainee; pilot jargon | yes | on |
| **Exam** | silent assessment | safety-only; no briefing | no | **off**; auto **Exam summary** (`practice=True`) |

Trainee/Pilot gate progression via `FORCE_REPEAT` when a graded leg ends below tolerance (same `phase_passed` criteria as before). Escalating scaffolding on attempt 2/3 remains a future enhancement.

### 7.3 Deviation-reactive coaching (user requirement)

Decided escalation: **guide back first, then offer retry.** Concretely:

1. Detect: geometry already gives cross/along/heading-offset; add a "deviation state" classifier — ON_PROFILE → DRIFTING (existing nudges handle) → OFF_PATTERN (outside pattern box or >45° wrong heading for >10 s) → LOST (far/diverging).
2. OFF_PATTERN/LOST suspends the circuit script (phase detector freezes; script callouts stop — no more "level off at 1,100 feet" while pointing at the sea) and switches MAVIS to **vectoring**: live, recomputed instructions from the aircraft's actual position — "no problem at all — let's bring it home; turn gently left until the runway is on your left window… keep coming… that's it, hold that heading." This is the flagship *reactive* behavior: instructions derived from where the trainee IS, not where the script says they should be.
3. If not re-established within ~90 s (or trainee sounds/acts stuck), offer the checkpoint: "or, if you like, I can take us back to downwind and we'll try fresh." In strict mode the agent may decide the retry itself.
4. Re-entry: once back within the pattern box on a sensible heading, resume the script at the matching phase (the self-healing detector already supports re-entry — reuse it).

### 7.4 Voice input (next phase, user answers incorporated)

Push-to-talk, fully local Whisper. Answers to the user's specific questions:
- **Button on the joystick? Yes** — the Warthog has ~19 HID buttons the existing pygame layer can already read; bind one (e.g. pinky switch) as PTT via the device profile YAML. Also add an on-screen hold-to-talk button on the tablet as fallback (mic lives in the tablet; stream audio over the existing WebSocket).
- **Wake word?** Not needed with PTT (that's its advantage). Offer later as an alternative mode via openWakeWord (local) — "MAVIS?" — for hands-busy moments.
- **Respond only to the trainee's voice?** Yes, feasible locally: enroll a 10-second voice sample at lesson start, embed with a speaker-verification model (SpeechBrain ECAPA-TDNN or resemblyzer), and reject transcriptions whose speaker similarity falls below threshold. Combined with PTT this makes bystander chatter at an airshow a non-issue. Design it as a filter stage: PTT gate → VAD → speaker check → Whisper → chat router (which already exists and needs zero changes).
- **Status (Jul 2026, verified live):** **real enrolment + hold-to-talk STT + joystick HID PTT are all wired.** Mic WAV → ECAPA voice store (`data/voices/`) → `faster-whisper` → existing `/chat`. The joystick chain: Warthog button (sim PC) → `tools/hid_forwarder.py` (`--ptt-button N`, `--find-button` to discover; default device match "stick" because BOTH Warthog units contain "Warthog" in their name) → `ptt` field in the UDP stream → `ControlSample.ptt` → WebSocket state → the browser mirrors the hold-to-talk gesture (`window.joyPtt`). Needs `input.mode: udp`, one manual mic-permission tap, and the tablet tab foregrounded. The tablet button is now a compact mic icon. Wake word remains deferred.

### 7.5 ACS-guided instruction (designed, not built)

The *Assessment* and *Decision* boxes of §7.1 are specified in **`ai-instructor/docs/acs-guided-instruction.md`**, grounded in FAA-S-ACS-6C (Private Pilot, Airplane). In short: the ACS PDF is extracted once into a verbatim element pack plus hand-reviewed bindings (element → telemetry metric + tolerance); an explicit leg-based **path model** replaces scattered circuit geometry; a deterministic **evaluator** scores each active element against its tolerance band, predicts where it will be in a few seconds, and logs every excursion; a deterministic **Tier 1 planner** guides from predicted bands and next-leg previews; the **LLM (Tier 2)** runs only at decision points (leg entry/exit, repeated errors, off-path), setting focus and policy knobs, never on the per-second loop. Modes are graduated (Visitor coaching only · Trainee ACS × 1.5 · Pilot/Exam exact ACS). The ACS unsatisfactory criteria replace `phase_passed`, and Exam adds a checkride-style S/U sheet alongside the score. §7.3 vectoring becomes path geometry.

## 8. Suggested lesson roadmap (schema-driving)

The lesson YAML schema should generalize to (in teaching order — L1 already exists):

- **L0 Cockpit familiarization:** no flying; MAVIS points at the controls guide, trainee wiggles each control, app verifies via control positions. Great first-contact demo, near-zero risk.
- **L1 Traffic pattern** ✅ (this build).
- **L2 Climbs, turns & descents:** straight-and-level, standard-rate turns to headings, target climbs/descents — needs target-heading/altitude exercises rather than pattern geometry → introduces an "exercise" primitive (setup position + goal + tolerance + timeout), which is also exactly what strict-mode remedial drills need. Build L2 and remedial exercises as one feature.
- **L3 Slow flight & stalls:** safety-critical coaching, altitude-floor guard, recovery scripts.
- **L4 Engine failure in the pattern:** decision-making, teleport-heavy (repeat from downwind), pairs perfectly with checkpoints.
- **L5 Night circuit:** scripted day→night transition (via `night_event` in lesson YAML), visual illusions, instrument cross-check.

## 9. Repo map (ai-instructor/)

```
main.py                    wiring, tick loop (10 Hz), chat intent router, checkpoints/retry
adapters/sim_base.py       Telemetry model + SimAdapter protocol
adapters/sim_msfs.py       ALL sim-specific code: SimConnect vars/events, remote cfg,
                           computed V/S, experimental set_position
adapters/sim_mock.py       self-flying compliant student (release/comply/save/restore)
adapters/input_hid.py/_udp central raw-input; device profiles in profiles/devices/
core/phase_detector.py     pattern geometry + self-healing transitions
core/monitors.py           coaching checks (grace period, cooldowns via engine)
core/lesson_engine.py      phase events, optional night trigger, load_phase (teleport)
coach/llm_coach.py         queue, speech lock, stale suppression, Ollama, Q&A, extract_time
coach/phrases.py           PERSONA + all templates (incl. switch-flaps + INTRO) — edit tone here
voice/tts.py               kokoro/piper/pyttsx3, synth-to-wav for tablet playback
web/server.py + static/    FastAPI, WS state push, single-file UI, cockpit schematic SVG
lessons/lesson01_pattern.yaml   runway/pattern/total_circuits/targets
profiles/aircraft/c172sp_g1000.yaml   v-speeds, flaps style, facts (feeds Q&A)
tools/hid_forwarder.py     optional sim-PC-side raw HID → UDP
```

## 10. Invariants — please preserve

1. **Gentle persona is the default** as specced in `phrases.py` — it's the product's soul, and it's what every first-time visitor meets. The pilot/indirect/drill personas (§12.2) are deliberate opt-ins gated behind Settings + pilot jargon; drill must never be reachable from Visitor mode.
2. **Fully local** — no cloud calls, ever, for the core loop.
3. **Every LLM path has a deterministic fallback**; demos must survive Ollama dying mid-flight.
4. **Deterministic where precision matters** (clock parsing, safety callouts, phase detection); LLM where judgment/flexibility matters.
5. **Layman language** in UI and speech; the trainee may have never touched a plane.
6. **Config-over-code** for sims, aircraft, devices, themes, lessons, video sources, and instructor modes (`profiles/modes.yaml`).
7. Speech: serialized, rate-limited, stale-suppressed. Never let MAVIS contradict the trainee's instruments or talk over herself.
8. Mock mode must always be able to run the full showcase hands-free — it's the development and rehearsal harness.

## 11. Phase-transition reference (exact conditions)

All transition logic lives in `core/phase_detector.py::tick()` and is driven by three geometry values computed in `geometry()` (all in the runway frame):

- **`along`** — metres past the runway threshold along the runway heading. Positive = beyond the threshold in the take-off direction; the approach/final side is negative.
- **`cross`** — metres to the *pattern side* of the extended centreline (positive = pattern side). Computed as `(left − centerline_offset_m) × side`, so the `pattern.centerline_offset_m` calibration shim shifts it (see §6 note / A5).
- **`hdg_off`** — signed difference between aircraft true heading and runway heading, −180..180°.

Two derived flags: **`on_dw_hdg`** = `|(|hdg_off| − 180)| < 30` (pointing roughly opposite the runway, i.e. a downwind heading); **`away`** = `cross > 250` (displaced onto the pattern side). `turn_sign = −side` (in left traffic, turning toward the pattern drives `hdg_off` negative).

| From → To | Fires when |
|---|---|
| PRE_TAKEOFF → TAKEOFF_ROLL | on ground **and** (`ias > 15` kt **or** `throttle > 0.8`) |
| PRE_TAKEOFF / TAKEOFF_ROLL → CLIMB_OUT | airborne **and** `alt_agl > 50` ft (also catches an already-airborne start) |
| CLIMB_OUT → CROSSWIND | (`alt_agl > min(300, xwind_agl×0.9)` **and** `turn_sign·hdg_off > 30`) **or** (`away` **and** `|hdg_off| > 45`) |
| CLIMB_OUT → DOWNWIND *(self-heal)* | `on_dw_hdg` **and** `away` (caught the downwind leg without ever registering crosswind) |
| CROSSWIND → DOWNWIND | `on_dw_hdg` |
| DOWNWIND → BASE | `30 < |(|hdg_off| − 180)| < 160` (turned 30–160° off the downwind heading) **and** `along < 200` |
| BASE / DOWNWIND → FINAL | `|hdg_off| < 30` (lined up) **and** airborne **and** `|cross| < 400` **and** `along < 0` |
| FINAL → FLARE_TOUCHDOWN | `alt_agl < 30` ft |
| FLARE_TOUCHDOWN → ROLLOUT | on ground |
| ROLLOUT/FLARE → CLIMB_OUT *(touch-and-go, multi-circuit only)* | airborne again **and** `alt_agl > 50` **and** `circuit < total_circuits` → `circuit += 1` |
| ROLLOUT → DEBRIEF | `circuit ≥ total_circuits` **and** `ias < 25` (L1: `total_circuits = 1`) |
| FINAL/FLARE/ROLLOUT → DEBRIEF *(safety net)* | on ground **and** `ias < 10` held for > 6 s |

With the current lesson (`xwind_agl = 600`), `min(300, 540) = 300`, so **leaving CLIMB_OUT requires climbing through ~300 ft AGL *and* starting a ≥30° turn toward the pattern side.** Sitting on the runway, or flying straight ahead without turning, keeps the tracker in CLIMB_OUT *by design* — that is the answer to "after a redo of the take-off I was put back at the start of the runway in CLIMB_OUT and couldn't get to CROSSWIND." You get out by taking off again, climbing past ~300 ft, and banking into the crosswind turn (left, for RWY 02). (Teleport placement itself is still experimental on MSFS — see §6.)

### 11.1 Proactive turn cues (guidance only — they do NOT change phase)

`core/monitors.py` now voices a turn prompt *before* each corner so the trainee knows when to bank, instead of only being corrected after overshooting. Each cue re-fires at most every ~10 s while its condition holds and is re-armed on every phase change. Thresholds are tunable:

| Cue | Phase | Condition |
|---|---|---|
| TURN_CROSSWIND | CLIMB_OUT | `alt_agl ≥ xwind_agl − 20` (≈580 ft AGL) |
| TURN_DOWNWIND | CROSSWIND | `alt_msl ≥ pattern_alt − 80` (≈1,020 ft) |
| TURN_BASE | DOWNWIND | `along < −300` (≈300 m past the threshold, approach side) |
| TURN_FINAL | BASE | `|cross| < 250` **and** `|hdg_off| > 30` |

Turn cues and the rotate callout are spoken **verbatim** (in `coach/llm_coach.py::VERBATIM`) so timing-critical instructions are never re-worded or delayed by the LLM — this is what fixed the ambiguous "that's 55 knots" rotate line.

## 12. Instructor modes & jargon (splash → menu → lesson)

The tablet opens on a **splash screen** → a **main menu** with **three mode cards** (Visitor / Trainee / Pilot) → the lesson. Mode selection loads a policy from `profiles/modes.yaml` via `POST /session` → `main.py::configure_session` → `phrases.configure(flap_style, jargon, guidance=…)`.

**Training mode (menu cards)** — ids `visitor` | `trainee` | `pilot` (legacy aliases: `casual`→`visitor`, `serious`→`pilot`). Exam is a Settings toggle that starts `exam` policy:

| Mode | Expected jargon | Coaching filter | Guidance | `force_repeat` | `score_caps` | Chat |
|------|-----------------|-----------------|----------|----------------|--------------|------|
| Visitor | layman | `turn_by_turn` (all events incl. `TURN_*`) | direct | no | no | on |
| Trainee | pilot | `reactive` (clearance + safety + debrief only; no PHASE/TURN/nudges) | direct | yes | yes | on |
| Pilot | pilot | `reactive` (same quiet filter) | **indirect** (unused while quiet; reserved for future deviation vectoring) | yes | yes | on |
| Exam | pilot | `silent` (safety only; no briefing) | — | no | no | **off**; auto practice/Exam summary |

Exam is **not** a main-menu card. Enable it under **Settings → Instruction → Exam mode**. When armed, an amber **Exam** pill shows on the menu and lesson map chrome; any mode-card click starts a silent assessment.

Forced-repeat criteria (Trainee/Pilot) live in `main.py::phase_passed`: `GATED = {CROSSWIND, DOWNWIND, BASE, FINAL}`; speed ±15 kt; DOWNWIND alt ±150 ft; FINAL `|cross| < 120 m`; `MAX_ATTEMPTS = 2`.

**Jargon (Settings panel, persisted in `localStorage`):**

- **Layman** (default) — plain beginner language; `PERSONA_LAYMAN` + base `TEMPLATES`.
- **Pilot** — professional phraseology; `PERSONA_PILOT` + `PILOT_TEMPLATES`. With Pilot mode’s `guidance: indirect`, `PERSONA_INDIRECT` + `INDIRECT_TEMPLATES` overlay symptom→control hints (e.g. overspeed → “check your power”).

`configure(flap_style, jargon, guidance, tone)` rebuilds `PERSONA` + `TEMPLATES` from a pristine base each call (see §12.2 for the full four-axis engine).

**Coupling / confirm dialog:** modes and jargon remain independent, but each mode has an **expected** jargon (table above). On mismatch the menu shows a 3-option confirm: **back to menu**, **proceed as-is**, or **switch jargon and proceed**. Settings also expose dark/light theme and date/time + name formats.

### 12.1 Layman ↔ Pilot script — full difference table

Every coached callout and its two wordings. `INTRO` is deliberately **not** overridden — it is spoken by the avatar video's own audio, so it must stay identical in both jargon settings. Keep this table in sync with `coach/phrases.py` (`TEMPLATES` / `SWITCH_FLAPS` = layman, `PILOT_TEMPLATES` = pilot; Pilot-mode indirect overlays are listed under `INDIRECT_TEMPLATES`).

| Code | Layman | Pilot |
|---|---|---|
| PHASE_ENTER_TAKEOFF_ROLL | Here we go. Push the throttle lever all the way forward, nice and smooth. | Cleared for take-off. |
| PHASE_ENTER_CLIMB_OUT | Lovely, we're flying. Relax your grip and hold {speed} knots for me. | Airborne. Climb at {speed} knots, Vy, wings level. |
| PHASE_ENTER_CROSSWIND | Beautiful turn. Keep climbing up to {alt} feet — that's our circuit height. | Crosswind leg — continue the climb to circuit altitude, {alt} feet. |
| PHASE_ENTER_DOWNWIND | Nicely done. Level off at {alt} feet and settle at {speed} knots. | Downwind. Level at {alt} feet, {speed} knots, run your pre-landing checks. |
| PHASE_ENTER_BASE | Good, fourth side of the rectangle. Second notch of flaps when you're ready, and let it descend. *(switch: "…let it descend gently.")* | Base leg — reduce power, flaps as required, {speed} knots on descent. |
| PHASE_ENTER_FINAL | There's your runway. Full flaps — the last notch — {speed} knots, small corrections only. *(switch: "…Hold {speed} knots, small corrections only.")* | Final — full flaps, {speed} knots on approach, hold the centreline. |
| PHASE_ENTER_FLARE_TOUCHDOWN | Pull the throttle gently back, eyes to the end of the runway, let it settle. | Over the threshold — power to idle, flare, and hold it off. |
| PHASE_ENTER_ROLLOUT | You did it. Let it roll, gentle brakes, take a breath. | Touchdown — centreline with rudder, brakes as required. |
| PHASE_ENTER_ROLLOUT_TG | *(multi-circuit lessons only)* Wheels down — beautiful. Keep it rolling… | Touch and go — flaps set for take-off, full power, rotate at Vr. |
| PHASE_ENTER_DEBRIEF | And relax. That was a real circuit, flown by you. Let's talk about it. | That's a full stop. Brakes to a stop — we'll taxi clear and debrief the circuits. |
| ROTATE_NOW | We're at flying speed now — ease the stick back gently and let it lift off. | Vr, {vr} knots — rotate. |
| ROTATE_LATE | Ease the stick back a little now, nice and easy, the aeroplane is ready. | Rotate now — ease back on the yoke. |
| TURN_CROSSWIND | You're at six hundred feet — ease into a gentle {dir} turn now. | Turn {dir} onto crosswind now, maintain the climb. |
| TURN_DOWNWIND | Now roll {dir} again onto the long leg, and level off around {alt} feet. | Turn {dir} onto downwind, level at {alt} feet. |
| TURN_BASE | Time to turn {dir} now, onto the short leg before the runway. | Turn {dir} onto base. |
| TURN_FINAL | Turn gently {dir} to line up with the runway ahead of you. | Turn {dir} onto final, line up with the centreline. |
| SPEED_FAST | We're a touch quick at {ias}. Ease the nose up a little, toward {tgt} knots. | Speed {ias}, high — reduce toward {tgt} knots. |
| SPEED_SLOW | Speed's drifting low at {ias}. Gently lower the nose toward {tgt} knots. | Speed {ias}, low — lower the nose to {tgt} knots. |
| ALT_HIGH | We've floated about {off} feet high. Ease it back down, no rush. | {off} feet above circuit altitude — reduce and re-establish. |
| ALT_LOW | We're sitting a little low. Push the throttle forward a touch and hold it there. | Below circuit altitude — add power and regain it. |
| ABEAM_FLAPS | We're halfway along this side. Pull the power back a little, and set the first notch of flaps. *(switch: "…put the flaps down.")* | Abeam the numbers — carb heat, power back, flaps as required. |
| FLAPS_REMINDER | How about the {notch} of flaps now, whenever you're comfortable. *(switch: "Pop the flaps down for me…")* | Select landing flap when you're ready. |
| BANK_STEEP | Let's make that turn a little gentler. Shallow, easy turns work best here. | Watch the bank — keep it to a rate-one turn. |
| CENTERLINE_DRIFT | Drifting a little {side}. Small correction, then wings level again. | Drifting {side} of the centreline — correct back. |
| SINK_HIGH | Coming down a bit fast. Push the throttle forward a little, and soften the descent. | High sink rate — add power and reduce the descent. |
| SMOOTH_HANDS | Small, soft movements. You have more time than it feels like. | Smaller, smoother inputs — anticipate the corrections, don't chase them. |
| NIGHT_ANNOUNCE | *(future night lesson — not used in L1)* | *(future night lesson)* |
| NIGHT_AFTER | *(future night lesson)* | *(future night lesson)* |
| TELEPORT | No problem at all — everyone gets a second try with me. Taking you back so we can fly that part again. | Repositioning to the last checkpoint — we'll re-fly that segment. |
| TELEPORT_FAIL | Hmm, I couldn't move the aeroplane just now. Let's simply fly the pattern around and set up again… | Unable to reposition — we'll fly the circuit round and set up for another approach. |
| FORCE_REPEAT | Let's give that part another go and really nail it — taking you back. | That leg wasn't quite to standard — we'll re-fly it. Repositioning. |
| BRIEFING_1 | We'll fly a big gentle rectangle around the field — take off, four easy turns, and land back on the same runway. One circuit, full stop. | We'll fly one circuit — take-off, crosswind, downwind, base, final, full stop. |
| BRIEFING_2 | There's no rush and no way to fail today — I'll talk you through every step… | I'll call the checks and speeds; you fly the aeroplane. When you're ready, full power for take-off. |
| INTRO | *shared — spoken by the avatar video's audio; not overridden* | *(same)* |

### 12.2 Persona & wording engine — four axes, one `configure()` (BUILT)

Everything MAVIS says is shaped by **four independent axes**, combined in
`coach/phrases.py::configure(flap_style, jargon, guidance, tone)`. `main.py::configure_session`
calls it on every session start with values from the mode policy + Settings.

| Axis | Values | Source | What it changes |
|---|---|---|---|
| `jargon` | `layman` (default) / `pilot` | Settings (persisted), mode-expected per §12 | Vocabulary: warm beginner English vs professional phraseology |
| `flap_style` | `notches` / `switch` | aircraft profile (`flaps.control_style`) | Flap wording matches the physical hardware (lever detents vs up/down toggle) |
| `guidance` | `direct` / `indirect` | mode policy (Pilot mode) | Indirect = examiner style: never names the symptom number, prompts the causal control — "check your power" instead of "you're at 95 knots" |
| `tone` | `warm` (default) / `drill` | Settings → Instructor tone | Drill = hardcore combat-instructor persona with profanity **on corrections only** — routine callouts, clearances, turn cues and briefings stay clipped-professional |

**Layering (order matters, later wins):** pristine `LAYMAN_TEMPLATES` snapshot →
`SWITCH_FLAPS` → `PILOT_TEMPLATES` → `INDIRECT_TEMPLATES` → `DRILL_TEMPLATES` /
`DRILL_INDIRECT_TEMPLATES`. Any code missing from an overlay **falls through to the
layman line**, so a new callout only *requires* a layman template. `configure()` always
rebuilds from the snapshot — never mutates a live dict (an earlier stale-copy bug).

**Matching personas** for the LLM path: `PERSONA_LAYMAN`, `PERSONA_PILOT`,
`PERSONA_INDIRECT`, `PERSONA_DRILL`, `PERSONA_DRILL_INDIRECT` — `configure()` points the
module-global `PERSONA` at the right one, and the Coach reads `phrases.PERSONA` at call
time, so a mid-session reconfigure takes effect immediately. All personas answer identity
questions with the acronym (`MAVIS_ACRONYM` = "Multimodal Aviation Verbal Instruction System").

**Gating rules (enforced in `configure_session`, don't relax them):** `indirect` requires
pilot jargon; **drill requires pilot jargon** and is never available in Visitor mode —
a first-time member of the public must never meet the drill sergeant. Intro videos are
selected by *mode* (`INTRO_BEGINNER`/`INTRO_TRAINING` matching the two mp4s' baked-in
audio), never by tone or jargon — the text shown in chat must match the video's voice.

**Interaction with the speech pipeline:** timing-critical codes
(`llm_coach.py::VERBATIM` + all `PHASE_ENTER_*`) skip the LLM and speak the active
template verbatim — so for those, the axes act purely through the template overlays.
Free-phrased events and chat Q&A go through the LLM with the active `PERSONA`.
Number-freshening and stale-suppression (§10/§16.6) run regardless of axis choices.

**Extending it:** new callout code → add the layman line to `TEMPLATES` (mandatory),
pilot/indirect/drill lines optional (fall-through covers them); add to `VERBATIM` if the
line is timing-critical; update the §12.1 table. New tone/persona → new `PERSONA_*` +
overlay dict + a branch in `configure()` + a gating decision in `configure_session`.

## 13. Agentic layer — Phase 1: conversational tool-router (BUILT)

The first slice of the §7 agentic architecture is implemented in `ai-instructor/agent/`:

- `tools.py` — a `Tool` (name, description, typed `params` schema, async handler, `needs_confirm`) and a `ToolRegistry` that `describe()`s the tools for the LLM prompt and `validate()`s the arguments the model proposes (type-coerces, enforces `enum`s, rejects missing required args). Validation is the safety gate — bad/incomplete args mean *don't run it*.
- `router.py` — `IntentRouter.decide()` resolves an utterance in three graceful steps: (1) optional deterministic matchers → (2) the local LLM's tool selection (the "understand intent" hand-off: it returns `{"tool": name|null, "args": {…}}` only) → (3) plain Q&A. The LLM never touches the sim; it only *names* a tool, one per utterance.

**Design principle:** the *agent* is a deterministic orchestrator that owns the tools, validation and safety; the *LLM* is the intent brain; the *tools* are typed, validated actions. This is the same `Tool`/schema abstraction Phase 3's autonomous policy loop will reuse.

**Wiring (`main.py`):** the existing rule-based intents in `chat()` are unchanged and still run **first** (authoritative for the must-be-reliable cases — explicit clock times, "go back", "show the guide"). The router runs only as a **fallback before free Q&A**, catching fuzzy requests the rules miss ("it's a bit bright, can you dim it" → `set_time_of_day`; "let me have another crack at that landing" → `retry_phase`; "where's the flap lever" → `controls_guide`; "chuck some clouds in" → `set_weather`). Registered Phase-1 tools: `set_time_of_day`, `retry_phase`, `controls_guide`, `set_weather`. Everything degrades to Q&A if Ollama is slow/offline (invariant #3 preserved).

**Cost / known edge:** on the fall-through path there is one extra low-temperature LLM routing call before Q&A. It's kept separate from `coach.answer` on purpose, so the fact-grounded Q&A is unaffected; merging them (Phase 1.1) would trade Q&A grounding for a little latency, so it was left separate.

### 13.1 Weather on MSFS 2020 — verified (why `set_weather` is a stub)

Double-checked against current sources: **you cannot change MSFS 2020 weather live via SimConnect.** All `SimConnect_Weather*` functions (incl. `WeatherSetObservation`) are deprecated and non-functional — MSFS 2020 replaced the FSX/P3D METAR-injection model with a new meteoblue-based engine, and `WeatherSetObservation` returns `SIMCONNECT_EXCEPTION_WEATHER_INVALID_METAR`. There is **no** SimConnect "set weather preset" API (it's a long-standing open request on MSFS DevSupport), and the ambient-weather sim-vars (wind, precip, etc.) are **read-only** — you can read the weather but not set it. The Python-SimConnect library MAVIS uses doesn't wrap the weather API either.

Practical options, none of them a clean live SimConnect change: (a) select a weather preset in-sim **before** the lesson; (b) ship custom `.WPR` preset files, which require a sim **restart** to change; (c) a third-party engine (REX Weather Force / Active Sky) via its own IPC. Time-of-day *is* settable (ZULU events — the day→night showcase already uses this), which is why that works and weather does not.

**Decision:** keep `set_weather` as a graceful "coming soon" tool. If weather becomes a hard requirement, the realistic paths are a pre-flight preset chosen at lesson setup, or moving to MSFS 2024 (which reportedly exposes a fuller weather API). The deck's "Change weather — PLANNED" tag should be read with this constraint in mind.

### 13.2 MSFS 2024 weather hook (config-driven, ready to test)

MSFS 2024's SDK is more capable than 2020's (it adds `SimConnect_ExecuteAction` and a real weather system), **but** as of early 2026 programmatic weather-preset loading is still *not* a settled, documented one-liner — developers are actively "struggling with the weather API" on MSFS DevSupport, and no simple community-confirmed call exists. So rather than hardcode a function that may not exist (or crash the connection with a wrong ctypes signature), `MSFSAdapter.set_weather(condition)` is **config-driven and safe**: it fires whatever SimConnect event or settable var you confirm works at the sim, via `config.yaml → sim.weather`:

```
sim:
  weather:
    method: none        # none (MSFS 2020) | event | simvar
    presets:
      rain: { event: "<confirmed key event>", value: 0 }   # method: event
      # or   { var:   "<settable sim var>",   value: 4 }   # method: simvar
```

`set_weather` always returns a bool and never raises (so testing is risk-free); it returns False on MSFS 2020 / when unconfigured, and the `set_weather` agent-tool then falls back to the "coming soon" line. **To finish it at an MSFS 2024 sim:** discover the real trigger (SimConnect Inspector in the SDK, or the SimConnect API Reference weather/actions section), drop the event/var into config, set `method`, and it works — no code change. Enum presets exposed to the agent: `clear, cloudy, rain, storm, fog`.

**Migration note — what changes moving the `msfs` adapter from 2020 → 2024:** the most likely break is **Python-SimConnect's bundled `SimConnect.dll`**, which is built for the 2020/FSX protocol — MSFS 2024 may need the 2024 SDK's DLL (or a compatibility mode), so verify `connect()` first. Also re-verify: the ZULU time-set events, the standard sim-vars the adapter reads (position, IAS, heading, flaps, on-ground, RPM, brakes, LOCAL_TIME), teleport via settable vars (already experimental, more likely to differ), and note that custom `(>F:KeyEvent)` calculator events reportedly behave differently in 2024. Vertical speed is self-computed so it's version-robust. Aircraft/airport availability differs between titles but the aircraft/lesson YAML is config-driven. A clean way to encode any 2024-specific differences later is a `sim.variant: msfs2020 | msfs2024` flag gating them.

## 14. AI scoring & leaderboard (BUILT — `core/scoring.py`, `core/leaderboard.py`; re-score vs reference §15.9 pending)

Goal: when a lesson ends, grade the trainee on how well they flew and post a score to a leaderboard. Feeds the "AI grading" end-product vision and reuses the agent infrastructure.

### 14.1 Principle — deterministic metrics, LLM debrief

The **numbers are deterministic**, computed from telemetry/track/monitor events (fair, reproducible, un-gameable). The **LLM only writes the debrief narrative** on top of those numbers ("smooth landing, but you drifted high on downwind") and may add a friendly headline grade. The LLM never invents the scores — a leaderboard needs comparable numbers. Consistent with invariant #4.

### 14.2 When a run is scored

Two ways a run ends (per §7 modes):
- **Completed** — L1 reaches `DEBRIEF` (full stop after the single circuit). → compute, show scorecard, **post** to the board.
- **Back / quit mid-lesson** — confirm dialog first: *"Leave without finishing? You won't get a leaderboard score. [Keep flying] [Leave anyway]."* If they leave, show a **practice summary** (same metrics, marked *practice — not posted*) but do **not** post it. Rationale: the board stays to complete, comparable runs, and it can't be gamed by quitting before a bad landing. The scorecard shows a dedicated note: *"Because the circuit was not completed, this result will not be posted to the leaderboard."* (not appended to the LLM debrief).

**Incomplete runs — landing N/A (implemented Jul 2026):** if the trainee quits before any touchdown is captured, **Landing** is **`N/A`** (not 0) on the scorecard display — but landing's **weight still counts in the total** as **0**, so an early quit cannot inflate the grade. **S** grade is never awarded on incomplete runs. Grade bands apply to that penalised total.

### 14.3 Metrics & how each is computed (from data we already have)

| Metric | Computed from | How |
|---|---|---|
| **Track accuracy** (green-vs-grey) | flown track + ideal pattern polygon `[[0,0],[upA,0],[upA,off],[-1500,off],[-1500,0],[0,0]]` (pattern metres, same as the map) | per-tick perpendicular distance of the aircraft to the nearest ideal-pattern segment → RMS → score |
| **Speed discipline** | `tel.ias` vs `monitors.tgt_speed[phase]` | per-tick error vs the phase target; % of airborne time within ±band / smooth curve |
| **Altitude discipline** | `tel.alt_msl` vs `pattern_alt` (downwind), glidepath vs `alt_agl` (final) | per-tick deviation → score |
| **Landing quality** *(L1 headline)* | telemetry at the `on_ground` transition in FLARE/ROLLOUT | touchdown **sink rate** (`vs`), **centreline** (`geom.cross`), **speed** (`ias`), and **approach stability** (were final params in band the last ~15 s) |
| **Smoothness / finesse** | raw HID `ctrl` or `aileron/elevator` deltas; `SMOOTH_HANDS` events | control-reversal / jerk rate → score |
| **Retries** | `attempts` dict / teleport count | not a 0–100 score — a count shown as its own column; see §14.5 |

These **five scores + the retry count** are exactly the leaderboard columns (§14.8). Other signals (procedure/config timing, bank/coordination, safety-event caps, lap consistency) are intentionally **deferred** — easy to add later since the Scorer already sees the data.

### 14.4 Score mapping, weights, grades

Each metric → **0–100** (100 = dead-on). Per-tick error `e` mapped with a smooth curve, e.g. `100·mean(exp(−(e/scale)²))`, or simply "% of time inside the band." Weighted sum → **total 0–100**. Default L1 weights over the five metrics (in the lesson YAML so each lesson grades what it teaches): **Landing 30, Track 25, Altitude 20, Speed 15, Smoothness 10** (= 100). Grade bands A/B/C/D for friendliness. When a metric is **not applicable** for display (e.g. landing on an incomplete circuit), the scorecard shows **N/A** but that metric still contributes **0** at its full weight to the total.

### 14.5 Retries → **grade cap** (chosen mechanism)

Retry count is its own column, **0 is best**, and it **hard-caps the achievable grade** rather than a sliding penalty. Default cap bands (tunable in lesson YAML), applied in **Trainee** and **Pilot**; in **Visitor** and **Exam** retries are shown but do **not** cap:

| Retries | Max total |
|---|---|
| 0 | 100 (A available) |
| 1 | 85 |
| 2 | 78 |
| 3 | 70 |
| ≥4 | 60 (pass) |

The trainee's *actual* flying (post-teleport reps) is what's scored; retries then cap it. So a beautifully-flown but twice-retried run is capped at 78 — a clean run always wins.

### 14.6 Visitor / Trainee / Pilot · Session vs All-time · data model

- **Separate boards** for Visitor, Trainee, and Pilot (Exam runs are `practice=True` and never posted).
- Legacy `casual` / `serious` modes map to Visitor / Pilot.
- MAVIS keeps posted runs **in memory only** (its pop-up board shows runs since it started) and publishes them in `state["leaderboard_posts"]`. The **all-time** board is IRIS's: its bridge saves each posted run to Postgres (`leaderboard_entries`) and IRIS's Leaderboard page serves it, paged and searchable. Each entry carries a **basic score** (vs the planned circuit) and, when an expert reference was flown, an **expert pilot score** (the same run scored against that expert's track, speeds and downwind height).
- Entry schema:

```
{ "name": "Jun", "mode": "pilot", "lesson": "lesson01_pattern", "ts": 1783600000,
  "total": 82, "grade": "B", "retries": 1, "capped": true,
  "scores": { "track": 90, "speed": 85, "altitude": 88, "landing": 74, "smoothness": 92 } }
```

Sort by `total` desc; show `retries` so ties / cleanest-run are visible.

### 14.7 Name capture

Use the trainee-name field from the main menu (already exists). If blank at scoring time, show a quick arcade-style *"enter your name/initials"* on the scorecard before posting — good for exhibitions.

### 14.8 Flow & screens

1. Run ends (completed → auto; Back → confirm) → the **Scorer** produces a scorecard.
2. **Scorecard popup** (full-screen): per-metric bars + retries + total + grade + the LLM debrief. Buttons: **Fly again** (restart the lesson), **View leaderboard**, **Scoring guide**, **Main menu**. (There's no "back to the same run" once it's over — *Fly again* is a fresh run.)
3. **Leaderboard popup** — a lightweight overlay dismissed by **clicking anywhere outside** (like the ⓘ Controls Guide); it has *no navigation buttons of its own*, so closing it returns you to wherever you opened it from (the scorecard, or the main menu). Tabs Visitor│Trainee│Pilot × Session│All-time. Row, left→right: **Rank · Name · Track · Speed · Altitude · Landing · Smoothness · Retries · Total**, with the just-finished run highlighted. Opened from the scorecard's *View leaderboard* and from the main menu.
4. **Scoring guide popup** — same click-outside-to-close overlay. Explains what each of the five metrics measures, the grade bands, and the retry → grade-cap table (§14.5) so trainees know exactly how they're judged. Opened from the scorecard, the leaderboard, and the main menu.
5. **Main menu** gets **Leaderboard** and **Scoring guide** entries so anyone can view them without flying.

### 14.9 Data collection (the Scorer)

A `core/scoring.py::Scorer`, driven by a `scoring:` block in the lesson YAML (weights, tolerances, retry-cap bands, grade bands):
- `reset()` at lesson start; `sample(tel, geom, ctrl, phase, events)` every tick — accumulates per-phase error stats (track distance, speed/alt error, bank time, smoothness), watches for the landing touchdown capture, and counts `correction`/`safety` events;
- `note_retry(phase)` when a teleport/forced-repeat fires;
- `finalize(mode, retries) -> ScoreCard` at the end — sub-scores, caps, total, grade.

### 14.10 Integration points (for the build)

- **`main.py` ticker**: call `scorer.sample(...)` each non-spike tick; `scorer.note_retry(...)` in the retry/force-redo paths; on transition to `DEBRIEF`, `finalize()` → post + set `state["scorecard"]`.
- **`web/server.py`**: `POST /finish` (Back → practice summary, no post) and the leaderboard is pushed in `state` (runs posted since MAVIS started, in memory; IRIS stores the all-time board in Postgres).
- **`index.html`**: a scorecard popup (buttons: Fly again / View leaderboard / Scoring guide / Main menu), plus **leaderboard** and **scoring-guide** popups that close on outside-click (reuse the controls-guide overlay pattern — no buttons inside them); a Back button on the lesson view wired to the confirm dialog; **Leaderboard** and **Scoring guide** entries on the main menu.

### 14.11 Build slices & future hooks

Slices: (1) deterministic Scorer + lesson-end/Back hooks + scorecard popup with LLM debrief; (2) session leaderboard + navigation (Main menu / Fly again); (3) all-time persistence + main-menu access. Future: the same `ScoreCard` is where **eye-tracking** attention metrics and full **AI auto-debrief/replay** plug in (deck §"end product").

## 15. Reference profiles — "fly the answer" (expert flight profiles)

Customer idea: instead of only a geometric ideal circuit, let an **expert pilot fly the lesson once and record it as the "answer."** The trainee then flies against a chosen reference — the grey dotted line, the numbers MAVIS coaches to, and the score all come from that reference. Multiple experts (e.g. *Maverick*, *Iceman*) can each have profiles.

**Build status (Jul 2026):** Slice 1 + partial slice 3 UI + **§15.11.2 coaching MVP**. Demo expert JSON, PIN manage runs, Record profile UI (client-only). **Fly like now drives spoken targets, gauge bands, and turn-cue timing** via `Monitors.apply_reference`. Instructor save-to-disk and scoring vs expert path remain in §15.11.1 / slice 4.

### 15.1 What a "reference profile" is

A **reference** is the thing a run is guided toward and graded against. Two kinds:

- **Planned** (default, today): the geometric ideal pattern polygon + the fixed target speeds/altitudes from the lesson + aircraft YAML.
- **Expert profile**: a **single recorded run** by an expert. It carries a **free-text display name** the instructor sets and can edit anytime (default suggestion = the callsign, e.g. *Maverick*; they might add a note like *Maverick — smooth*). Underneath, each profile has an immutable id (slug + timestamp) plus metadata (lesson, aircraft, recorded-by, date, run #), so several runs by the same expert coexist without a rigid filename — the picker shows the display name and adds a small date/run chip only when one expert has more than one. A profile is **bound to a lesson + aircraft** and only selectable for a matching pair.

Chosen behaviour (from Q&A): a selected expert profile drives **path + targets + turn points (full)** — the grey line, the numbers MAVIS coaches to, and where the turns happen.

### 15.2 One recorder for experts *and* trainees

The same **`FlightRecorder`** captures both. Per tick (~5–10 Hz) it stores: `t, lat, lon, alt_msl, alt_agl, ias, vs, hdg, bank, pitch, flaps, throttle/rpm (power), aileron, elevator, rudder, on_ground, phase`. Recording trainee runs too is what makes the **re-score / compare** feature possible (§15.9).

### 15.3 Representation — indexed by progress, not time

The trainee flies slower/faster than the expert, so runs can't be compared by timestamp — they're compared by **where you are in the circuit**. Each recording is reduced to a **progress-indexed reference curve**: for each small step of progress through each phase (using the existing pattern coordinates `(along, cross)` + phase), store the reference `position (lat/lon)`, `ias`, `alt`, `vs`, `power`, `stick`, `rudder`. Guidance and scoring look the reference up at the trainee's **current phase + position**, never by time. **Turn points** (the `along/cross/alt` where each phase changed) are derived from the recording via the existing phase detector.

### 15.4 Profile file & registry

Stored as JSON at `profiles/experts/‹name›.json`:

```
{ "name": "Maverick-L1-Run3", "callsign": "Maverick", "lesson": "lesson01_pattern",
  "aircraft": "c172sp_g1000", "recorded_by": "instructor", "ts": 173..., "quality": "good",
  "samples": [ ...raw ticks... ], "reference": { ...progress-indexed curve... },
  "turn_points": { "CROSSWIND": {...}, "DOWNWIND": {...}, ... },
  "targets": { "CLIMB_OUT": {"ias": 79}, "DOWNWIND": {"ias": 82, "alt": 1100}, ... } }
```

A registry lists profiles filtered by the current lesson + aircraft; `Planned` is always first/default.

### 15.5 Access control — instructor PIN (chosen)

`config.yaml` gets `instructor: { pin: "…" }` — local only (on-prem, no internet). The trainee UI has **no** record button. A discreet **🔒 Instructor** entry on the **main menu** (footer, beside Settings / Leaderboard) opens a PIN prompt; the PIN is checked server-side. **Implemented:** PIN is required on every instructor-area open; closing the overlay calls `POST /instructor/lock` (no persistent unlock across visits). That unlocks the **instructor area**: *Record reference*, *Manage runs*, and *Instructors* (add / edit / delete accounts). This is a deterrent suited to a local exhibition/training box, not hardened security — good enough for on-prem, and nothing leaves the machine.

**PIN policy (Jul 2026):** minimum **6 characters**; each instructor has a distinct PIN in `data/instructors.json`. Error messages are deliberately generic (no “PIN already in use”) so a failed create does not confirm guesses.

**Future — proper PIN security (todo):** store salted hashes only (never return PINs to the client admin list); constant-time verify; separate “wrong PIN” from “account exists” without leaking uniqueness on create (e.g. always succeed with delay, or use username+PIN pairs). Until then, length + generic errors are the mitigation.

**Instructor vs trainee (important):** Visitor / Trainee / Pilot on the main menu are **trainee training modes**, not role selectors. Exam is a Settings toggle (Instruction → Exam mode), not a fourth card. Instructor recording is a **separate path** via PIN → Instructor area → *Record reference* → lesson + **Start recording**.

### 15.6 Recording flow (instructor) — auto-record, no button

Once unlocked, the instructor picks the lesson and taps **Start recording** in the *Record reference* tab — no wizard, no main-menu round-trip. **UI built (Jul 2026):** PIN → Instructor area → Record reference → Start recording → one circuit → review overlay (**Save reference** / **Fly again** / **Exit recording**). Backend capture is still stubbed (§15.11.1).

Recording **auto-starts** when the first phase begins (take-off roll) and **auto-stops** when the last phase ends (full stop / DEBRIEF); the `FlightRecorder` captures the whole circuit.

**What MAVIS does while recording** (answering "does guidance stay the same?"): the **lesson phase tracking stays on** — it's needed to segment the flight into legs and derive the turn points — and MAVIS still **announces the phases** ("downwind…") and keeps **safety callouts** (rotate, excessive bank). But the **corrective nudges are suppressed** (no "you're 5 knots fast", no centreline chasing): the expert is *defining* the answer, so MAVIS must not push them toward the default numbers. The **default (planned) grey line is shown** as a light shape reference so experts fly roughly the same circuit, but it is not enforced.

On the full stop a **review** appears — the flown path + key per-phase stats — with **Save reference** / **Fly again** / **Exit recording**. *Fly again* clears the chat, restarts at the runway, and replays the intro for another capture attempt. *Exit recording* returns to the instructor area. Names are auto-suggested and editable later.

### 15.7 Selecting a reference (trainee)

During a **trainee** lesson, the **Fly like** picker in the lesson strip (not the main menu) chooses *Planned* (default) or any expert profile matching the lesson + aircraft (shown as callsign + run). The choice ships with the session config and immediately drives the grey line (§15.8). Guidance and scoring against the expert reference are **not fully wired yet** (§15.11).

### 15.8 What the selection changes

- **Grey line**: Planned → today's geometric polygon; Expert → the expert's recorded path (lat/lon) drawn as the grey dotted line. The active path is pushed in `state`; the map draws whichever is active.
- **Adaptive guidance (chosen: numbers + turn points)**: the monitors/coach read **targets from the active reference** instead of fixed YAML — per-leg target speed/altitude = the expert's, and turn cues fire at the **expert's** turn points (height/position). So MAVIS coaches "hold 82 knots" and "turn crosswind now" the way *Maverick* flew it. Reuses the phase engine; deterministic reflexes (rotate, safety, stale-suppression) are unchanged. *(Continuous path-following/vectoring to the exact curved line is deliberately deferred to the deviation-coaching/vectoring work in §7.3 — bigger, and can nag.)*

### 15.9 Scoring against a profile + re-score/compare

**Architectural change:** scoring becomes **post-hoc from the stored trainee recording**, parameterised by the reference: `score(trainee_recording, reference) → ScoreCard`. So every trainee run is recorded (same `FlightRecorder`), and the Scorer no longer only streams aggregates.

- Metrics vs the reference: **track** = distance to the reference *path* (not the geometric polygon); **speed/altitude** = deviation from the reference's value at the same **progress point**; **landing** = vs the reference touchdown; **smoothness** stays intrinsic to the trainee.
- **Compare at the summary:** the trainee picks another profile → the server recomputes `score(same recording, other reference)` → the scorecard updates, no re-flying. Different references legitimately give different scores — that's the point.
- **Leaderboard:** each entry records **which reference** it was graded against (a Reference column, and/or boards split per reference), so comparisons are apples-to-apples.

**UI stub (Jul 2026 — backend deferred):** the scorecard includes a **Compare against** dropdown populated with *Planned circuit* plus expert profiles. Changing the selection updates scores with **demo offsets** per reference (animated bar transitions) and the note *"Scores shown are compared against …"*. Wire-up: `POST /score/rescore` calling `score(recording, selected_reference)` for real numbers.

### 15.10 Guards

Profiles are locked to a matching lesson + aircraft. Reject empty/too-short recordings at save. A trainee flying a wildly different path still scores fine mechanically (reference lookup is by phase/progress; big deviations → low track/speed/alt scores).

### 15.11 Build slices & status

| Slice | Scope | Status |
|---|---|---|
| **1** | `FlightRecorder` + recording format; silently record trainee runs (groundwork for re-score) | ✅ Done (`core/recorder.py`; sampled every tick in `main.py`) |
| **2a** | Demo expert profiles on disk + registry; PIN-gated manage runs (rename/delete); grey line from expert track | ✅ Done (`core/reference_profiles.py`, instructor area) |
| **2b** | **Record profile UI flow** — wizard, arm session, menu recording card, in-flight strip, review stub | ✅ Done (client-only; `settings.recordingDraft` in `localStorage`) |
| **3** | **Record profile backend** — arm API, auto-capture, coaching suppression, save/review | ⬜ Todo (§15.11.1) |
| **4** | Refactor **Scorer → `score(recording, reference)`**; `Planned` reference at parity with today | ⬜ Todo |
| **5** | Guidance + turn cues read targets/turn points from active reference (§15.8) | ✅ MVP done (§15.11.2 — targets + turn thresholds; not full progress curve) |
| **6** | Summary compare (re-score vs another profile); leaderboard reference tagging | ⬜ Todo |

#### 15.11.1 Record profile backend — implementation todos

These complete slice **3** after the navigation UI (§15.6). Order is intentional — each step should be demo-able before the next.

- [ ] **15.11.1.1 Arm session (server)** — `POST /instructor/record/arm` with `{ lesson_id, callsign, display_name }`. Requires valid PIN unlock (`instructor_unlocked`). Sets `session["recording"] = { armed: true, … }` and pushes `state.recording` so the client can drop `localStorage` draft. Reject if lesson not playable or aircraft mismatch. `POST /instructor/record/cancel` clears arming.

- [ ] **15.11.1.2 Start recording flight (server + client)** — When the instructor starts a recording flight (not a trainee mode card), `configure_session` or a dedicated `POST /instructor/record/start` sets `session["recording"]["active"] = true`, `mode` irrelevant for scoring (no leaderboard post). Client sends arm metadata; server is source of truth. Hide trainee scorecard path for this run.

- [ ] **15.11.1.3 Auto-capture lifecycle** — On `PHASE_ENTER_TAKEOFF_ROLL` while `recording.active`: `recorder.reset()` and mark `capturing = true`. On `DEBRIEF` / full stop: mark `capturing = false`, stash `recorder.to_dict(meta)` in `session["recording"]["pending"]`, push review payload to state (samples count, duration, segments, phase_stats). Reject save if too few samples / no airborne time (§15.10).

- [ ] **15.11.1.4 Coaching suppression while capturing** — When `recording.capturing`: keep phase-enter announcements + safety reflexes (rotate, excessive bank); **suppress** corrective monitor events (speed/alt/centerline/smoothness nudges). Trainee/Pilot forced repeats must not fire during instructor capture. Document behaviour in `main.py` beside `phase_passed` / coach submit gate.

- [ ] **15.11.1.5 Review screen (real data)** — Replace review stub: draw flown path on a mini map or reuse main map snapshot; show per-phase stats from `recorder.phase_stats()`. Wire **Save** → `POST /instructor/record/save` with optional final `display_name`; server writes `profiles/experts/{id}.json` (§15.4), registers in `ReferenceRegistry`, `_sync_refs_to_state()`. **Discard** → drop pending capture, clear recording session. **Fly again** → reset lesson to runway, keep same arm metadata, `recorder.reset()`.

- [ ] **15.11.1.6 Profile id + run numbering** — On save: generate slug id (`{callsign_slug}_run{n}` or timestamp suffix), auto-increment `run_no` per callsign+lesson, set `recorded_at`, `demo: false`, build progress-indexed `reference` curve + `turn_points` + `targets` from recording (§15.3 — can be a follow-on sub-task if raw samples save first).

- [ ] **15.11.1.7 Client sync** — Remove reliance on `localStorage` for armed session once server arm exists; instructor overlay and menu banner read `state.recording`. In-flight strip shows **Capturing…** when `state.recording.capturing` (pulse dot). On debrief, open review overlay from WebSocket state, not practice scorecard.

- [ ] **15.11.1.8 Tests / mock demo** — Mock-mode path: arm → fly one circuit → review shows non-empty preview → save creates JSON → new entry appears in Manage runs and Fly like picker. Verify demo seed does not overwrite instructor-saved files on restart.

#### 15.11.2 MVP showcase — “Fly like Iceman — textbook” changes coaching

**Demo goal:** In the lesson, switch **Fly like** from *Planned circuit* to *Iceman — textbook*. The grey line moves (already works), and **MAVIS’s spoken targets change** — phase callouts (“level at 1080 feet, 82 knots”), speed/altitude nudges, and gauge green bands all follow Iceman’s numbers instead of the default lesson YAML. No instructor recording, no scoring refactor, no progress-indexed curve required for this slice.

**Already done:** expert JSON on disk, registry, Fly like picker, `POST /reference` + map track, `state.targets` → gauge bands (fed from `Monitors.targets_for` each tick).

**Not needed for this MVP:** §15.11.1 (record/save flow), §15.9 scorer refactor, continuous path-vectoring, LLM persona rewrite, leaderboard reference column.

| Priority | Task | Why |
|---|---|---|
| **A** | **`targets` on profiles** — add `"targets": { "DOWNWIND": {"ias": 82, "alt": 1080}, … }` to demo JSON (`iceman_run1`, `maverick_run1`, …). `Planned` = today’s aircraft + lesson values (85 kt / 1100 ft downwind). | Gives each expert **different numbers to coach to**. Hand-author for demo; real recordings can populate later (§15.11.1.6). |
| **B** | **`ReferenceProfile.targets_for_phase(phase)`** — load `targets` from JSON; fall back to lesson/aircraft defaults for `planned` or missing keys. | Single lookup the monitors can call. |
| **C** | **`Monitors.apply_reference(prof)`** — override `tgt_speed`, `pattern_alt`, optional `turn_points` (e.g. `TURN_BASE` when `along < -240` for tight Iceman vs `-300` planned). Called from `_apply_reference()` in `main.py`. | All existing monitor events (`SPEED_*`, `ALT_*`, `PHASE_ENTER_*` data, turn cues) automatically use the active reference — **no phrase.py changes** if templates already use `{speed}`, `{alt}`, `{tgt}`. |
| **D** | **Hot-swap on `set_reference`** — when trainee changes Fly like mid-lesson, re-call `apply_reference` (already async via `/reference`). | Showcase: switch profile during flight and hear the next nudge use new targets. |
| **E** | **One audible “profile switch” line** (optional, ~30 min) — on reference change, `coach.say` once: “Following Iceman — textbook from here.” | Makes the demo obvious to an audience without reading the gauge. |
| **F** | **`turn_points` (optional v1.1)** — JSON thresholds for `TURN_BASE` / `TURN_FINAL` per profile. | “When to turn” like Iceman; defer if time-constrained — speed/alt alone is enough for a convincing MVP. |

**Suggested demo numbers (C172 L1, noticeable but realistic):**

| Phase | Planned | Iceman — textbook | Maverick — wide |
|---|---|---|---|
| CLIMB_OUT / CROSSWIND | 74 kt | 74 kt | 76 kt |
| DOWNWIND | 85 kt @ 1100 ft | **82 kt @ 1080 ft** | **88 kt @ 1120 ft** |
| BASE | 75 kt | **72 kt** | **78 kt** |
| FINAL | 65 kt | **63 kt** | **67 kt** |

**Verify:** mock flight, Visitor mode, switch Fly like at downwind — phase line + next `SPEED_*` nudge + speed/height gauge bands should match Iceman row. Map dotted line already differs per profile.

**Explicitly defer:** scoring track metric vs expert path; post-hoc `score(recording, reference)`; instructor record/save (§15.11.1).

#### 15.11.3 Later slices (unchanged intent)

4. Refactor **Scorer → `score(recording, reference)`**; **`Planned`** implementation at parity with today's streaming scorer.
5. **Reference-driven guidance** — monitors/coach read targets and turn points from active reference (§15.8).
6. **Summary compare** + leaderboard reference column (§15.9). **UI only (Jul 2026):** scorecard dropdown + note; server `score(recording, reference)` re-score **deferred**.

### 15.12 Deferred / future

Averaging multiple runs of one expert into a smoother reference; live path-vectoring (with §7.3); instructor curation / quality flags; recording expert eye-tracking (with the future eye-tracking work).

## 16. Voice enrolment & speaker verification (BUILT — `voice/stt.py`, `voice/speaker.py`, `/voice/*` endpoints)

Push-to-talk voice input (§7.4) must respond **only to the trainee**, not to bystanders at an airshow. **Enrolment** builds a per-trainee voice print so recognition is accurate and gated to that person.

### 16.1 Flow (on choosing a mode)

Selecting a training mode runs a **voice gate** before the lesson starts:

1. Read the trainee **name** (main-menu field). **Blank → skip voice entirely**, proceed on default recognition (chosen default — least friction for exhibitions).
2. Name present → look up the **voice store** for that name:
   - **Enrolled** → inform "Voice profile found for ‹name›" and proceed.
   - **Not enrolled** → offer a choice: **Enrol** (higher accuracy) or **Continue without** (default recognition).
     - *Enrol* → the trainee reads the enrolment phrase aloud → capture → store the embedding under the name → "Voice enrolled" → proceed.
     - *Continue without* → proceed immediately (default recognition; no extra confirm).
3. Then the usual **jargon-mismatch confirm** (if any) → start the lesson. (Voice = who you are; jargon = lesson wording — voice gate runs first.)

### 16.2 Enrolment phrase (chosen)

*"Pack light, chase sunsets, and follow the road wherever curiosity pulls you."* (~10 s at a normal pace). One utterance is enough; optionally repeat once for robustness.

### 16.3 Tech stack (all local — keeps the on-prem / no-internet invariant)

- **Capture:** browser `MediaRecorder` (tablet mic) → stream over the existing WebSocket to the laptop.
- **VAD (trim silence):** Silero VAD or `webrtcvad`.
- **Speaker embedding (enrol + verify):** SpeechBrain **ECAPA-TDNN** (`spkrec-ecapa-voxceleb`), or **Resemblyzer** for a lighter option. Enrol = store the embedding; verify = cosine-similarity of the utterance embedding vs the enrolled print, accept only above a threshold.
- **PTT speech-to-text:** `faster-whisper` (CTranslate2 Whisper) on the RTX 5090.
- **Voice store:** a small **local** file (`data/voices.json` / `.npz`) mapping name → embedding. Nothing leaves the machine.
- **Router:** the transcript feeds the existing chat/agent router (§13) unchanged.

### 16.4 Verification at PTT time (implemented)

Embed the spoken utterance and cosine-compare to the enrolled print; **accept above threshold, else ignore** (bystander). If the trainee is *not* enrolled, fall back to a looser default (any clear speech) — lower accuracy, more false accepts, which is exactly why enrolment is offered.

### 16.5 Demo scope (built now) vs deferred

**Now (implemented):** real tablet mic capture (WAV), enrolment phrase (*Pack light, chase sunsets…*), live waveform, server-side ECAPA embedding store (`data/voices/`), fail-on-silence, playback confirmation, tablet hold-to-talk (mic icon) → speaker verify (if enrolled) → `faster-whisper` → existing `/chat`. Voice gate runs before jargon confirm; skip proceeds straight into training. **Joystick HID PTT bind: done** (see §7.4 status — forwarder `--ptt-button`, UDP `ptt` field, browser mirrors the hold).
**Deferred:** WebSocket binary audio streaming; wake word; name-collision handling via unique id rather than raw name.

### 16.6 Latency (measured Jul 2026 + fixes)

Measured stack on first field test: STT ~10 s + Ollama 3–5 s + Kokoro 1–2 s ≈ **15 s** button-release → voice. Findings:

- **The 10 s STT was CPU, not architecture.** `device: auto` asks `torch.cuda.is_available()`; plain `pip install torch` on Windows is CPU-only (speechbrain pulls it in), so Whisper-medium silently ran on CPU. Fix: CUDA torch (`--index-url …/whl/cu128` — the RTX 5090 is Blackwell) + `nvidia-cublas-cu12 nvidia-cudnn-cu12` for CTranslate2. GPU medium ≈ 0.3–0.8 s/clip. `stt.py` now logs per-clip compute time + device and warns loudly on CPU. Kokoro rides the same torch → also faster on GPU.
- **Ollama:** `keep_alive: -1` pins the model in VRAM (no 5-min-idle reload spikes mid-lesson). Generation is capped (`num_predict: 80`); `qwen2.5:7b` is the next knob if needed.
- **Speaker verify runs concurrently with transcription** (`/voice/ptt`); a rejected speaker just discards the transcript.
- Expected after CUDA fix: **~4–5 s** total. Next structural step (designed, not built): **sentence-streaming** — stream Ollama tokens, synthesize + play the first sentence while the rest generates → first spoken word ~2–2.5 s.
