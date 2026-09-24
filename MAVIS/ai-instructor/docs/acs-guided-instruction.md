# ACS-Guided Instruction — design

**Status:** design, not built. Extends §7 of `ai-flight-instructor-design.md` ("Is this agentic?").
**Source standard:** FAA-S-ACS-6C, *Private Pilot for Airplane Category Airman Certification Standards* (Nov 2023). Every tolerance quoted below was read from that edition; re-verify codes and numbers whenever the ACS is revised.
**Decisions taken (Sep 2026):**

| Question | Decision |
|---|---|
| How strict per mode | **Graduated** — Visitor: coaching only, no gating · Trainee: ACS × 1.5 · Pilot + Exam: exact ACS |
| What "planned path" covers | **General leg-based path model** — L1 circuit specified in detail; the same model carries ground-reference maneuvers and cross-country routes later |
| Exam output | **Both** — the 0–100 leaderboard score stays; Exam adds a checkride-style **S/U sheet** with ACS element codes. Codes are shown only in Pilot and Exam |

---

## 1. What the ACS gives us — and what it doesn't

The ACS is an **assessment standard**. It does not say how to teach; it says what "good enough" looks like. Its structure maps almost one-to-one onto the §7.1 *Assessment* box:

```
Area of Operation (I–XII)  →  Task (A, B, …)  →  Elements
                                                   K = Knowledge        ("demonstrates understanding of")
                                                   R = Risk management  ("identify, assess, and mitigate risk")
                                                   S = Skills           ("exhibits the skill to") ← most carry numeric tolerances
Code: PA.III.B.S5  =  Private Airplane · Area III · Task B · Skill 5
```

What we take from it, verbatim, and where each piece goes in MAVIS:

| ACS content | Example (6C) | MAVIS use |
|---|---|---|
| **Numeric skill tolerances** | PA.III.B.S5 "Maintain traffic pattern altitude, ±100 feet, and the appropriate airspeed, ±10 knots" | Evaluator bands (§4), leg grading (§7), Exam sheet (§8) |
| **Asymmetric tolerances** | PA.IV.A.S11/S13 "VY, +10/-5 knots"; PA.IV.B.S7 approach speed "+10/-5 knots"; PA.IV.B.S10 touchdown "within 400 feet beyond or on the specified point" | Evaluator supports `tol_hi`/`tol_lo` separately |
| **Qualitative skills** | PA.III.B.S3 "Correct for wind drift to maintain the proper ground track"; PA.IV.B.S10 "no side drift" | Measurable, but **we** choose the number → flagged `acs_numeric: false` everywhere it's shown |
| **Procedural skills** | PA.IV.B.S6 "Establish the recommended approach and landing configuration"; PA.IV.B.S11 "Execute a timely go-around…" | Event checks (flaps set by gate X; go-around decision) |
| **Risk elements** | PA.IV.B.R5 "Low altitude maneuvering, including stall, spin, or CFIT" | Become named monitor triggers / reflexes with an ACS citation |
| **Knowledge elements** | PA.IV.B.K1 "A stabilized approach, including energy management concepts" | Grounding text for chat Q&A and the Exam oral quiz |
| **Grading rules** (Appendix 1) | Unsatisfactory includes "Consistently exceeding tolerances…", "Failure to take prompt corrective action when tolerances are exceeded", "Any action or lack of action … that requires corrective intervention by the evaluator" | Directly become the leg-pass rule (§7) — replaces today's single-instant `phase_passed` |
| **Minimum elements rule** (Appendix 1) | Test "at least one knowledge element; at least one risk management element; and all skill elements unless otherwise noted" | Exam: all observable S elements from flight + a short post-flight oral quiz for ≥1 K and ≥1 R per Task (§8) |
| **Safety of flight** (Appendix 2) | Stall/spin awareness; positive three-step exchange of flight controls; realistic distractions | Reflex set; control-exchange phrasing for teleport/"I have control"; optional Exam distractions (future) |
| **Device rules** (Appendix 3) | Practical tests only in a Part 60 FSTD within an approved program; ATDs need an FAA LOA | MSFS + MAVIS is neither → every S/U sheet carries a **"practice only — not an FAA evaluation"** footer |

What the ACS does **not** give: when to speak, what to say, how early to warn, or how to recover someone who is lost. That stays MAVIS's pedagogy, which is the job of the guidance layers in §5.

---

## 2. Architecture at a glance

```
            OFFLINE (once per ACS edition)                    RUNTIME (10 Hz tick)
 ┌───────────────────────────────────────┐
 │ ACS PDF ──extract──► acs/pa_6c.json   │   (verbatim elements, generated, checksummed)
 │                     acs/bindings/*.yaml│◄── human-authored: element → metric, tolerance, phases
 └───────────────────────────────────────┘
                      │ loaded at lesson start
                      ▼
 Lesson YAML ─► PATH MODEL (legs) ─────────► progress s, cross-track, along, lookahead
 Reference profile (§15) ─┘                         │
                                                    ▼
 Telemetry + geometry ────────────────────► ACS EVALUATOR (deterministic)
                                             per active element: err, band n, trend,
                                             predicted n(t+τ), excursion log
                                                    │
               ┌────────────────────────────────────┼─────────────────────────────┐
               ▼                                    ▼                             ▼
      TIER 0  REFLEXES                     TIER 1  GUIDANCE PLANNER       TIER 2  LLM INSTRUCTOR
      (existing + ACS risk)                deterministic, every tick       event-driven, async
      rotate, bank, stall,                 picks ≤1 guidance intent        sets focus & policy knobs,
      sink, low alt                        from bands + lookahead          decides repeat/vector/retry
               │                                    │                             │ (validated JSON)
               └───────────────► Coach queue (speech lock, stale-suppression) ◄───┘
                                          │ phrased by template or LLM persona
                                          ▼
                                       Tablet voice + UI  ·  Scorer / S-U sheet  ·  IRIS events
```

The §7.1 rule is kept and sharpened: **deterministic reflexes and deterministic guidance, agentic judgment.** The LLM never sits on the per-second loop (§5.3 explains why: measured Ollama latency is 3–5 s, and the phrasing budget is 1 s). It changes *what the deterministic planner pays attention to* and *what happens at the end of a leg*.

---

## 3. The ACS pack (offline)

### 3.1 Extraction — verbatim, never paraphrased

`tools/acs_extract.py` (PyMuPDF, runs offline on a dev box):

1. Read the PDF text; match element lines with `^PA\.[IVX]+\.[A-Z]\.[KRS]\d+[a-z]?$`. The following line(s), up to the next code, are the element text. Task headers (`Task B.  Traffic Patterns`), Objectives, Notes and References are captured per Task.
2. Mark `[Archived]` elements (6C archived e.g. PA.IV.A.S10, PA.XII.A.S1) as `archived: true` and never bind them.
3. Tag class applicability from the Task title suffix — `(ASEL, AMEL)`, `(ASES, AMES)` — so seaplane/multi Tasks drop out automatically for a C172 land profile.
4. Write `acs/pa_6c.json` with the source edition, page numbers and a SHA-256 of the PDF. The runtime refuses a binding file whose `acs_edition` doesn't match.

The JSON is **data, not prompts**: the LLM sees element text only as quoted citations inside its context, never as instructions.

```json
{ "edition": "FAA-S-ACS-6C", "date": "2023-11", "pdf_sha256": "…",
  "tasks": {
    "PA.III.B": { "title": "Traffic Patterns", "area": "Airport and Seaplane Base Operations",
      "classes": ["ASEL","AMEL","ASES","AMES"], "page": 16,
      "elements": {
        "PA.III.B.S5": { "kind": "S", "text": "Maintain traffic pattern altitude, ±100 feet, and the appropriate airspeed, ±10 knots." },
        "PA.III.B.R1": { "kind": "R", "text": "Collision hazards." },
        "PA.III.B.K2": { "kind": "K", "text": "Traffic pattern selection for the current conditions." } } } } }
```

### 3.2 Bindings — the human-reviewed bridge to telemetry

The PDF tells us "±100 feet"; it doesn't tell us which telemetry field that means, in which phases, or relative to what. That mapping is authored by hand and reviewed by an instructor, one YAML per Task group (`acs/bindings/pattern.yaml`, `takeoff_landing.yaml`, …). Every S element falls into one of five **binding kinds**:

| Kind | Meaning | Example |
|---|---|---|
| `band` | continuous metric vs target ± tolerance, sampled over a window | PA.III.B.S5 altitude/airspeed |
| `band_own` | continuous metric, ACS is qualitative, **our** tolerance (`acs_numeric: false`) | PA.III.B.S3 ground track |
| `event` | a discrete thing must happen, possibly by a gate/deadline | PA.IV.B.S6 landing configuration by the stabilized gate |
| `capture` | measured once at a moment | PA.IV.B.S10 touchdown point / side drift / alignment |
| `unobservable` | can't be judged from sim data (radio calls, "clear the area", checklists) | PA.IV.B.S2 radio calls → `ui_confirm` or `oral`, never auto-failed |

```yaml
# acs/bindings/pattern.yaml
acs_edition: FAA-S-ACS-6C
task: PA.III.B                     # Traffic Patterns
legs: [CROSSWIND, DOWNWIND, BASE]  # where this Task is being flown
elements:
  PA.III.B.S5:
    - { id: alt,  kind: band, metric: alt_msl_ft, target: "{leg.alt}",   tol: 100,
        legs: [DOWNWIND], primary: true }        # crosswind is climbing, base descending: alt not held
    - { id: spd,  kind: band, metric: ias_kt,     target: "{leg.speed}", tol: 10, primary: true }
  PA.III.B.S3:
    - { id: track, kind: band_own, metric: cross_track_m, target: 0, tol: 150,
        acs_numeric: false, note: "ACS says 'proper ground track'; 150 m ≈ one wingspan-view off the line" }
  PA.III.B.S4:
    - { id: orient, kind: band_own, metric: path_state, target: ON_PATH, acs_numeric: false }  # §4.5
  PA.III.B.S2:
    - { id: pattern_proc, kind: event, check: legs_in_order_and_turn_direction }
  PA.III.B.S1: [{ kind: unobservable, assess: oral }]
  PA.III.B.S6: [{ kind: unobservable, assess: oral, note: "no AI traffic in L1" }]
risks:                                         # R elements → monitors with citations
  PA.III.B.R2: { monitor: path_state_lost }    # "loss of situational awareness, or disorientation"
  PA.III.B.R1: { monitor: none, assess: oral } # collision hazards: no traffic model yet
```

**Tolerance scaling lives in the mode, not the binding.** Bindings always carry the exact ACS number; `profiles/modes.yaml` gets `acs_tol_scale` (§6). That keeps one source of truth and means an ACS revision is a data update.

### 3.3 Coverage map — which Tasks each lesson exercises

For a C172 (ASEL). ✅ = specified in this doc · ◐ = model supports it, bindings authored with that lesson · — = not simulable / out of scope.

| Lesson (roadmap §8) | ACS Tasks | Status |
|---|---|---|
| L0 preflight / cockpit | II.A Preflight Assessment, II.B Flight Deck Management, II.C Engine Starting | ◐ mostly `unobservable`/`oral`; engine start is an `event` |
| **L1 traffic pattern** | **II.D Taxiing (ASEL), II.F Before Takeoff Check, III.B Traffic Patterns, IV.A Normal Takeoff and Climb, IV.B Normal Approach and Landing, IV.N Go-Around/Rejected Landing, XII.A After Landing** | ✅ |
| L2 climbs, turns, descents | VIII.A–D (±200 ft, ±20° hdg, ±10 kt; standard-rate turns, roll out ±10°) as the numeric basis | ◐ |
| Steep turns (Pilot) | V.A (entry alt ±100 ft, ±10 kt, bank ±5°, roll out ±10°) | ◐ |
| Ground reference | V.B rectangular course / S-turns / turns around a point (600–1,000 ft AGL, ±100 ft, ±10 kt) | ◐ path model `rect`, `arc` legs |
| L3 slow flight & stalls | VII.A (±100 ft, ±10° hdg, **+10/−0 kt**, bank ±10°; complete no lower than 1,500 ft AGL), VII.B/C stalls | ◐ |
| L4 engine failure | IX.B Emergency Approach and Landing (best glide ±10 kt; select field; plan path) | ◐ dynamic path to selected field |
| Cross-country | VI.A pilotage/DR (within 3 nm of route; checkpoints within 5 min of ETA; ±200 ft, ±15°), VI.C diversion, VI.D lost procedures | ◐ route legs |
| L5 night circuit | XI.A Night Operations (mostly K/R) + L1 Tasks at night | ◐ |
| — | I.* (ground knowledge), III.A radio skills, X multiengine, seaplane Tasks | — (oral only / not applicable) |

---

## 4. Path model and evaluator

### 4.1 The planned path is a list of legs

Today the "planned path" is implicit: a polygon in `scoring.py`, turn thresholds in `monitors.py`, phase geometry in `phase_detector.py`. The design makes it one explicit object, built from the lesson YAML **or** from the active reference profile (§15), so Planned and "Fly like Iceman" are the same code path.

```python
@dataclass
class Leg:
    id: str                    # "DOWNWIND", "RWY02_CLIMB", "CHK3_TO_CHK4", "RECT_S2"
    geom: LineSeg | Arc | PointRef   # lat/lon endpoints; arc centre+radius; or a point to orbit
    vprofile: Level | Climb | Descent | Glidepath   # target alt as f(progress)
    speed: SpeedTarget         # value + optional asymmetric tol override
    config: dict               # {"flaps": 10} expected by end/gate
    acs_tasks: list[str]       # ["PA.III.B"]
    entry: Condition           # used by the phase detector (existing self-healing rules)
    exit: Condition
    gates: list[Gate]          # e.g. stabilized-approach gate at 300 ft AGL on FINAL
    lookahead_s: float = 8.0   # how early to preview the next leg

@dataclass
class Path:
    legs: list[Leg]
    def locate(self, tel) -> PathFix: ...   # active leg, progress s∈[0,1], cross-track, along-to-go,
                                            # desired track, desired alt(s), time-to-leg-end
```

- **L1 instance:** CLIMB_OUT → CROSSWIND → DOWNWIND → BASE → FINAL legs generated from `pattern:` (offset, `base_turn_past_abeam_m`, altitude) or from `turn_points`/`targets` of an expert profile. Turns are short `Arc` legs, so the drawn grey line is the thing being evaluated (the current mismatch between the drawn rounded circuit and the square scoring polygon goes away).
- **Future instances:** V.B rectangular course = 4 `LineSeg`s around a field at 600–1,000 ft AGL; turns around a point = `Arc` with constant radius; a cross-country route = `LineSeg`s between checkpoints, each with an ETA (VI.A.S6 "within five minutes") and a route corridor (VI.A.S5 "within three nautical miles").
- The phase detector keeps owning *which phase we're in* (and its self-healing). The path model owns *where we should be inside it*. For L1, phase == leg id.

### 4.2 Per-element evaluation

For each element active on the current leg, every tick:

```
err      = actual − target(s)                       # target can vary with progress (glidepath)
n        = err / tol_hi   if err ≥ 0                # normalised band; handles +10/−5 asymmetry
         = err / tol_lo   if err < 0               #   |n| ≤ 1 ⇔ inside the ACS tolerance
rate     = d(err)/dt  (EMA over ~2 s)
n_pred   = (err + rate·τ) / tol                     # where it'll be in τ seconds
state    = IN (|n| < 0.5) · EDGE (0.5–1.0) · OUT (> 1.0)     with 0.1 hysteresis + 1.5 s dwell
```

`τ` (prediction horizon) is per metric and reflects how long a correction takes to show on the instrument: altitude 6 s, airspeed 8 s, cross-track 10 s, heading 4 s, bank 2 s. Tunable in the binding.

The **excursion log** is the key record: one entry per OUT episode — `{element, t_start, t_end, peak_n, time_to_first_correction, corrected_by: trainee|reflex|coach|never}`. "Time to first correction" is the first moment `sign(rate) = −sign(err)` with meaningful magnitude — i.e. the trainee started fixing it. The ACS says it looks for *prompt* correction, and this is how MAVIS measures it.

**Windowing:** sampling starts after the existing 8 s settle-in grace on each leg (unchanged — a leg entry is not an excursion), and pauses while a reflex or vectoring owns the airplane (§5.1, §5.5).

### 4.3 Asymmetric and gated elements in L1

| Element | Binding | Notes |
|---|---|---|
| PA.IV.A.S9 rotate at recommended speed | `capture` at lift-off: `ias − vr` ∈ [−3, +8] | existing `ROTATE_NOW`/`ROTATE_LATE` stay reflexes |
| PA.IV.A.S11/S13 VY +10/−5 kt to safe altitude | `band` on CLIMB_OUT/CROSSWIND, `tol_hi 10, tol_lo 5` | target from reference if active |
| PA.IV.A.S14 directional control + drift correction | `band_own` cross-track on runway + climb-out, tol 30 m | `acs_numeric: false` |
| PA.III.B.S5 pattern alt ±100 / airspeed ±10 | `band` DOWNWIND alt; all pattern legs speed | the core L1 element |
| PA.III.B.S3 ground track | `band_own` cross-track, tol 150 m | needs ground track (§10) to *coach* wind correction |
| PA.IV.B.S6 configuration + stabilized approach | `event` + **gate**: at 300 ft AGL on FINAL, flaps set, `|n_speed| ≤ 1`, `|cross| ≤ 60 m`, sink ≤ 1,000 fpm | the 300 ft figure is ours (industry VFR convention), flagged |
| PA.IV.B.S7 approach speed +10/−5 kt | `band` FINAL, `tol_hi 10, tol_lo 5` | "gust factor applied" → add ½ gust to target when wind data exists |
| PA.IV.B.S10 touchdown | `capture`: along from aim point ∈ [0, +122 m]; lateral drift ≤ 2 kt; heading vs runway ≤ 5°; on centreline ≤ 10 m | 400 ft = 122 m is ACS; drift/heading/centreline numbers are ours |
| PA.IV.B.S11 timely go-around | `event`: if the stabilized gate fails, expect power + climb within 5 s | L1 is full-stop; see §6.2 for how each mode handles it |
| PA.IV.N.* go-around | `band` VY +10/−5 after go-around, positive rate before flaps up | activates only if a go-around happens |

### 4.4 Deviation state (generalises §7.3)

```
ON_PATH   all primary elements IN/EDGE, cross-track < 1× tol
DRIFTING  any primary element OUT, or cross-track 1–2× tol          → Tier 1 cues handle it
OFF_PATH  cross-track > 2× tol for > 10 s, or heading > 45° off desired track for > 10 s
LOST      > 2 km from any leg or distance to path increasing for > 30 s while OFF_PATH
UNSAFE    any reflex of severity "safety" active                      → reflex owns the voice
```

`OFF_PATH` and `LOST` suspend normal leg guidance and start **vectoring** (§5.5). A `LOST` episode is also logged against PA.III.B.S4 "Maintain orientation with the runway" and risk PA.III.B.R2 "loss of situational awareness".

---

## 5. Guidance: three tiers

### 5.1 Tier 0 — reflexes (deterministic, pre-empt everything)

Existing: `ROTATE_NOW`, `ROTATE_LATE`, `BANK_STEEP`, `SINK_HIGH`, stale-suppression, the speech lock. New ones, each citing the ACS risk element it serves:

| Code | Trigger | ACS |
|---|---|---|
| `STALL_WARN` | sim stall-warning var true, or `ias < 1.1·vs1` airborne | PA.IV.A.R5 / IV.B.R5, Appendix 2 stall/spin awareness |
| `LOW_ALT` | < 300 ft AGL and not on FINAL/CLIMB_OUT, or below glidepath by > 150 ft on FINAL | IV.B.R5 (CFIT) |
| `FLAP_OVERSPEED` | flaps > 10° and `ias > vfe_full` (C172: 85 kt) | IV.B.S6 configuration |
| `BANK_STEEP` (existing) | > 45° → safety; > 60° → "wings level" | IV.A.R5 |

Reflexes are verbatim, never go through the LLM, and they **pause element sampling** for their duration plus 5 s. Their activation is logged as a `corrected_by: reflex` intervention — this is the sim equivalent of the ACS "corrective intervention by the evaluator," and it counts in grading (§7).

### 5.2 Tier 1 — the deterministic guidance planner (the spine)

Runs every tick; emits **at most one** guidance intent per decision window (≥ `min_speak_interval_s`). This is what makes MAVIS *guide* rather than *scold*: it speaks from **predicted** bands, and it **previews** the next leg.

Priority order (first match wins):

1. **Predicted OUT on a primary element** — `|n_pred| > 1` while `|n| < 1` → *anticipation cue* ("ease the nose down a touch now so we level at eleven hundred"). This fires **before** the tolerance is exceeded.
2. **OUT, trainee not yet correcting** — `|n| > 1` and `rate` not reducing it for > 2 s → correction cue (today's `ALT_HIGH`/`SPEED_FAST`, now band-driven).
3. **OUT, trainee is already correcting** → **stay quiet**, or at most "that's it, keep it coming" once. This fixes the classic nag of repeating a correction the trainee is already making (stale suppression catches only the *already fixed* case).
4. **Leg preview** — `time_to_leg_end ≤ lookahead_s` → next-leg brief (turn direction, target alt/speed, configuration). The existing `TURN_*` cues become a special case, fired from path geometry instead of hand-set thresholds.
5. **EDGE with a diverging trend** on the focus element (from Tier 2) → gentle nudge.
6. **Praise** — every primary element IN for ≥ 20 s after a correction → one "nicely held" (max once per leg). Positive feedback is a teaching behaviour too, and the gentle persona already calls for it.
7. Otherwise silence.

Each intent is a `CoachEvent` with `code`, `element` (ACS code), `n`, `n_pred`, `target`, `actual`, `direction`. Phrasing uses the existing templates (§12.1 of the main doc) extended with anticipation variants. So **with the LLM dead, MAVIS still guides by the ACS**, which keeps invariant #3.

What varies by mode is set by *policy knobs* (§6), not by different code: which priorities are enabled, the EDGE threshold that starts cues, and whether codes and numbers are spoken.

### 5.3 Tier 2 — the LLM instructor (event-driven, sets policy)

**Why not a 1 Hz LLM loop, as §7.1 sketched?** Measured end-to-end Ollama latency is 3–5 s (§16.6), and `llm.timeout_s` for phrasing is 1.0 s. A policy that arrives 4 s after the fact is worse than no policy at 80 kt. So the LLM is called **at decision points**, runs asynchronously, and its output changes the **parameters** the Tier 1 planner uses. It never drives a real-time callout.

**Decision points (triggers):**

| Trigger | LLM decides |
|---|---|
| Leg entry (+ during settle-in grace) | `focus_elements` for this leg (≤ 2, from the trainee's excursion history), `verbosity` (quiet / normal / talkative within the mode's range), optional one-line leg brief intent |
| Leg exit | `leg_verdict` suggestion (pass / let slide / repeat), a short debrief note to keep |
| Same element OUT on two consecutive legs | switch coaching technique: `technique: symptom → cause` (e.g. from "you're high" to "try a little less power on downwind") — i.e. the indirect style, applied pedagogically rather than only by mode |
| Deviation state → OFF_PATH / LOST | `vector` (default) vs `offer_retry` timing (§5.5) |
| Stabilized-gate failure on FINAL | in Pilot/Exam: nothing (Tier 1 has the go-around call); in Trainee: whether to call "go around" or coach the save (bounded by mode) |
| Trainee question (PTT or typed) | existing router/Q&A, now with the ACS element text as grounding |

**Input packet** (compact, ~400 tokens, built by the evaluator — the LLM never sees raw telemetry):

```json
{ "mode": "trainee", "jargon": "pilot", "leg": "DOWNWIND", "acs_task": "PA.III.B",
  "elements": [
    {"id":"PA.III.B.S5/alt","text":"Maintain traffic pattern altitude, ±100 feet…",
     "tol":150, "n":0.7, "trend":"diverging", "n_pred":1.2},
    {"id":"PA.III.B.S5/spd","tol":15, "n":-0.2, "trend":"steady"} ],
  "history": {"PA.III.B.S5/alt": {"excursions_this_lesson": 2, "median_time_to_correct_s": 9}},
  "last_spoken": {"code":"ALT_HIGH","s_ago":14},
  "attempt": 1, "legs_remaining": ["BASE","FINAL"] }
```

**Output** — JSON only, one of a closed set, validated by the existing `agent/tools.py` `ToolRegistry` (enums, bounds, required args):

```json
{ "action": "set_focus",
  "focus_elements": ["PA.III.B.S5/alt"],
  "verbosity": "normal",
  "technique": "cause",
  "note": "Climbs through pattern altitude on every level-off; coach power reduction before reaching 1,100." }
```

Allowed actions: `set_focus`, `set_verbosity`, `set_technique`, `leg_verdict`, `vector`, `offer_retry`, `insert_exercise` (future, §8 of the main doc), `none`.

**Guardrails:**

- Element ids must exist in the *active* binding — any other id is rejected. The LLM can't invent a standard or cite a Task that isn't loaded.
- Knob values are clamped to the mode's allowed range (Visitor can't be made silent; Exam can't be made talkative).
- `leg_verdict` is advisory: the deterministic rule in §7 is authoritative, and the LLM can only move a *borderline* leg (rule says within 10 % of the threshold) one step. The note is kept for the debrief.
- Any timeout, invalid JSON or rejected field → keep the previous policy (defaults = the mode's baseline). Nothing waits on the LLM.
- Temperature 0.2 for policy (it's a decision, not prose); a separate call from the phrasing LLM.

### 5.4 Phrasing (unchanged pipeline, new vocabulary)

Tier 1 intents go into the existing Coach queue: speech lock, correction queue-jump, stale-suppression, number-freshening at speak time. New template codes, each needing a layman line (mandatory) and optional pilot/indirect/drill overlays per §12.2:

| Code | Layman | Pilot | Indirect (Pilot mode) |
|---|---|---|---|
| `ANTICIPATE_LEVEL` | "We're coming up on circuit height — start easing the nose forward now." | "Approaching {alt} — begin level-off, reduce power." | "Approaching pattern altitude." |
| `ANTICIPATE_SPEED` | "Speed's creeping {dir} — a small nudge now saves a big one later." | "Speed trending {dir} of {tgt} — adjust now." | "Watch the trend." |
| `CORRECTING_OK` | "That's it — keep it coming." | "Good correction." | *(silent)* |
| `HOLD_PRAISE` | "Beautifully held — that's right on the numbers." | "Nicely held — within standards." | "Good." |
| `LEG_PREVIEW_*` | e.g. "Coming up: gentle left turn, then we let it descend." | "Next: left base, flaps 20, {speed} knots." | "Next leg in {s} seconds." |
| `STABLE_GATE_FAIL` | "This one isn't settled — let's add power and go round, no problem at all." | "Unstabilized — go around." | "Are you stabilized?" |
| `VECTOR_*` | §5.5 | | |

When the **focus element** changes (Tier 2 `set_focus`), Tier 1 cues for that element get the EDGE threshold lowered (e.g. 0.5 instead of 0.7) and, in Trainee/Pilot, can carry a one-clause reason taken from the element's K text ("…because a stabilized approach makes the landing predictable").

### 5.5 Vectoring back to the path (the §7.3 flagship, made concrete)

With an explicit path, vectoring is geometry, not script:

1. Find the **rejoin point**: the nearest point on the *active or next* leg that's at least 45 s of flight ahead (so the intercept is flyable), not behind the airplane.
2. **Intercept heading** = desired track at the rejoin point ± 30° toward it (45° if > 1 km off), capped to a standard-rate turn.
3. Tier 1 speaks one instruction at a time, from live geometry: turn direction → "keep coming" → "roll out, hold that" → "the runway will appear on your left window" → re-capture when `cross < 1× tol` for 5 s → resume normal guidance at the matching leg (the self-healing detector already supports re-entry).
4. After 90 s without progress (distance to path not decreasing), Tier 2 may `offer_retry`: "or I can take us back to downwind and we'll fly it fresh." In Pilot mode it may decide the retry itself (existing `FORCE_REPEAT`).
5. Element sampling pauses while vectoring. The episode is logged once against PA.III.B.S4 (orientation) with its duration; it's not also counted as a hundred altitude/speed excursions.

---

## 6. Modes — the graduated policy

`profiles/modes.yaml` gains an `acs:` block per mode. Nothing forks in code.

| | Visitor | Trainee | Pilot | Exam |
|---|---|---|---|---|
| `acs_tol_scale` | 2.0 (bands only drive coaching) | **1.5** (±150 ft / ±15 kt — matches today's `phase_passed`) | **1.0** (exact ACS) | **1.0** |
| Tier 1 priorities on | all (1–7) | 1, 2, 3, 6 · previews on first attempt only | 1 (only when `n_pred > 1.2`), 2 after 5 s uncorrected | none (reflexes only) |
| EDGE cue threshold | 0.5 | 0.7 | off | off |
| Tier 2 LLM | focus + verbosity + technique | + leg verdict advice | + may decide retries | **off during flight**; post-flight oral quiz (§8) only |
| Gating (forced repeat) | no | yes (§7) | yes (§7) | no (graded, never repeated — ACS: repetition of an unsatisfactory Task isn't permitted) |
| ACS codes visible | never | in debrief only, on tap | debrief + chat | S/U sheet |
| Unstable approach (§6.2) | MAVIS coaches the save; go-around only if unsafe | MAVIS calls "go around" | pilot is expected to go around unprompted | silent; graded on PA.IV.B.S11 |

### 6.1 Why graduated

Visitors should get the help but never meet a standard they didn't sign up for. Trainees learn the *shape* of the standard with a 1.5× cushion, which is also today's forced-repeat threshold, so their experience doesn't change on day one. Pilot and Exam hold the real numbers, so "I passed MAVIS Pilot mode" means something.

### 6.2 Go-around in a full-stop lesson

L1 is full-stop and has no multi-circuit logic enabled. A go-around triggered by the stabilized gate re-enters CLIMB_OUT (the detector's touch-and-go path already supports `circuit += 1`); `total_circuits` is raised to 2 for that run only. In Trainee, a go-around is scored as **correct decision-making** (IV.B.S11 satisfied) and doesn't count as a retry for the §14.5 grade cap.

---

## 7. Grading a leg — the ACS rule replaces `phase_passed`

Today `phase_passed` checks one instant at leg end (±15 kt, ±150 ft, `|cross| < 120 m`). The ACS grades over the **whole task** and names three failure patterns. They translate directly:

A leg **fails** (Trainee/Pilot → `FORCE_REPEAT`; Exam → element marked U) if **any** of:

1. **Consistently exceeding tolerances** — any primary element spends > 20 % of the sampled leg time OUT.
2. **Failure to take prompt corrective action** — any excursion with `time_to_first_correction` > 5 s (Pilot/Exam) or 8 s (Trainee), or any excursion never corrected before leg end.
3. **Corrective intervention** — a Tier 0 reflex of severity `safety` fired on this leg (bank > 45°, stall warning, low altitude).

Plus a Task-specific `capture` element failing at its moment (e.g. touchdown beyond +400 ft of the aim point).

**Borderline** (within 10 % of rule 1's threshold, or within 1 s of rule 2's) → Tier 2 may nudge the verdict one way and explain why. Everything is logged so the debrief can say *exactly* why: "Downwind: height was outside ±100 ft for 31 % of the leg; first correction took 9 s."

The 20 % / 5 s / 8 s values are **our** interpretation of words the ACS leaves to the examiner. They live in `acs/grading.yaml`, and the S/U sheet says so.

**Scoring (§14) link:** the same evaluator stream feeds the Scorer. `altitude`/`speed` sub-scores become "% of sampled time with |n| ≤ 1 at exact ACS tolerance" (independent of mode scaling, so leaderboards stay comparable), and `track` keeps its RMS curve against the path model. Landing uses the PA.IV.B.S10 capture. The weights and grade bands in the lesson YAML are unchanged.

---

## 8. Exam mode — the S/U sheet

Exam flies silently (reflexes only). On the full stop:

1. **Flight elements** — every bound S element for each Task flown gets **S** / **U** / **Not observed** (e.g. IV.N if no go-around was needed) / **Not evaluable in sim** (`unobservable` kind), with evidence: time out-of-tolerance, peak deviation, time to correct, and a clickable moment on the replay track.
2. **Oral quiz (≤ 2 minutes)** — the ACS minimum per Task is at least one K and one R element. MAVIS asks one question per flown Task, alternating K and R, chosen by the LLM from the bound element text ("PA.IV.B.K1 — what makes an approach stabilized, and why does it matter?"). The trainee answers by voice/typing. The LLM grades against the element text + the aircraft facts as **Satisfactory / Needs review**, never U — an LLM isn't a trustworthy oral examiner, so knowledge results are advisory and labelled that way.
3. **Task result** — a Task is U if any of its S elements are U. The Area is U if any Task is U (mirrors Appendix 1).
4. **Output** — scorecard popup gets an "ACS" tab next to the existing five bars; the 0–100 total, grade and leaderboard behaviour are unchanged (`practice=True` for Exam as today).

```
PA.III.B  Traffic Patterns ........................ S
   S5  altitude ±100 ft      S   in tolerance 94 %, worst +120 ft (4 s), corrected in 3 s
   S5  airspeed ±10 kt       S   in tolerance 100 %
   S3  ground track*         S   RMS 64 m            * MAVIS tolerance, not an ACS number
   S1, S6                    —   not evaluable in sim
PA.IV.B  Normal Approach and Landing .............. U
   S7  approach speed +10/−5 S
   S10 touchdown ≤ 400 ft    U   touched down 610 ft beyond aim point
Oral: PA.IV.B.K1 stabilized approach — Satisfactory (advisory)
Practice only — MAVIS is not an FAA-qualified FSTD or ATD (FAA-S-ACS-6C, Appendix 3).
```

---

## 9. Chat Q&A grounded in the ACS

- The router (§13) gets one new tool: `explain_standard(element_or_topic)` — "what do I need to hold on downwind?" → quotes PA.III.B.S5 verbatim, then explains in the active jargon.
- Free Q&A context adds the **active Task's** K/R element texts (short: ~10 lines), so answers stay on-topic and correct about what the standard says. If a question needs something the pack doesn't contain, the persona says so rather than guessing.
- Visitor mode never mentions codes; it may say "real pilots are tested to within a hundred feet here — you're doing great."

---

## 10. Telemetry additions (sim adapter)

The ACS asks about wind drift, side drift and coordination; `Telemetry` has none of these today. Add to `adapters/sim_base.py` (defaults keep the mock and old adapters working), read in `sim_msfs.py`, and emulate in `sim_mock.py` (including a configurable steady crosswind, so wind-correction coaching can be rehearsed):

| Field | MSFS SimVar (verify at the sim) | Needed for |
|---|---|---|
| `gs` ground speed, kt | `GROUND VELOCITY` | ETAs (VI.A.S6), wind triangle |
| `trk` ground track, deg true | `GPS GROUND TRUE TRACK` | III.B.S3 / IV.A.S14 drift — coaching "point a little into the wind" |
| `wind_dir`, `wind_kt`, `gust_kt` | `AMBIENT WIND DIRECTION`, `AMBIENT WIND VELOCITY` (gust: derive from variance) | IV.B.S7 gust factor; crosswind K elements |
| `beta` sideslip, deg | `INCIDENCE BETA` | coordination; "no side drift" at touchdown (with lateral ground speed) |
| `ball` | `TURN COORDINATOR BALL` | coordinated turns (V.A.S3, VIII.D) |
| `stall_warn` | `STALL WARNING` | Tier 0 `STALL_WARN` |
| `v_lat` lateral ground speed, kt | `VELOCITY BODY X` | PA.IV.B.S10 side drift |

---

## 11. State, UI and IRIS

- `state["acs"]` (5 Hz push): active Task code + title, per-element `{id, n, state}` for the gauges' green bands (bands = mode-scaled tolerance, so Trainee sees ±150 and Pilot ±100), the current deviation state, and the current focus element (a subtle highlight on that gauge).
- Map: the path model's legs replace the hard-coded grey polygon; in OFF_PATH the rejoin point and intercept line are drawn dashed.
- The debrief/scorecard adds the per-leg reasons (§7) and, in Exam, the S/U tab (§8).
- IRIS (`docs/INTEGRATION_PLAN.md`, Option A): excursion-log entries go out through the existing WS state as `coaching_events` rows with `acs_code`, `n_peak`, `t_start/t_end`, `time_to_correct` — a free, precise index into the eye-tracking replay ("where was the trainee looking during the 9 s before they corrected the altitude?").

---

## 12. Files

```
acs/pa_6c.json                  generated verbatim pack (checked in; regenerated by tool)
acs/bindings/*.yaml             hand-authored element → metric bindings (instructor-reviewed)
acs/grading.yaml                §7 thresholds (20 %, 5 s / 8 s, borderline band)
tools/acs_extract.py            PDF → pa_6c.json (offline)
core/path_model.py              Leg / Path / locate()  (replaces scattered geometry for guidance+scoring)
core/acs_eval.py                bands, prediction, excursion log, deviation state, leg verdict
core/guidance.py                Tier 1 planner (intents from evaluator + path)
agent/instructor_policy.py      Tier 2 trigger handling, packet builder, validated actions (reuses ToolRegistry)
coach/phrases.py                new template codes (§5.4) + overlays
profiles/modes.yaml             acs: block per mode (§6)
adapters/sim_*.py               telemetry additions (§10)
```

`core/monitors.py` shrinks: speed/altitude/centerline nudges and turn cues move to `guidance.py` (band-driven); rotate/bank/sink stay in monitors as Tier 0.

---

## 13. Build slices (each demo-able in mock mode)

| # | Slice | Done when |
|---|---|---|
| 1 | **Pack + L1 bindings** — extractor, `pa_6c.json`, bindings for II.D, II.F, III.B, IV.A, IV.B, IV.N, XII.A | loader validates edition + every bound code exists and isn't archived |
| 2 | **Path model** for L1 (planned + expert reference) | the map's grey line and the scorer's track metric come from `Path`; headless mock circuit scores within ±2 of today's 80/B |
| 3 | **Evaluator + excursion log** | mock's deliberate height overshoot produces one excursion on PA.III.B.S5/alt with a time-to-correct value |
| 4 | **Tier 1 planner** replaces nudges + turn cues | mock run: an `ANTICIPATE_LEVEL` fires *before* the overshoot reaches +100 ft; no repeated nag while correcting; LLM killed → identical behaviour |
| 5 | **§7 leg grading** replaces `phase_passed` | 30-run jittered torture test still passes; forced repeats fire only on rule 1/2/3 |
| 6 | **Tier 2 policy** (leg entry/exit triggers) | invalid/slow LLM output never changes timing; focus change is audible on the next leg |
| 7 | **Vectoring** (OFF_PATH/LOST) | mock variant that flies 90° off downwind is talked back and re-captured at the right leg |
| 8 | **Exam S/U sheet + oral quiz** | Exam run renders sheet with the Appendix 3 footer; score + leaderboard unchanged |
| 9 | Telemetry additions + crosswind mock | wind-drift coaching ("point a little into the wind") on a 10 kt crosswind mock run |
| 10 | Later lessons: bindings for VIII, V.A/B, VII, IX.B, VI.A | per-lesson |

Keep using the accelerated headless mock pattern for every slice (invariant #8).

---

## 14. Invariants (additions to §10 of the main doc)

1. **ACS text is quoted verbatim or not at all.** Paraphrase belongs in templates, not in anything labelled with an ACS code.
2. **Our numbers are always flagged.** Any tolerance or threshold the ACS doesn't state is `acs_numeric: false` / lives in `grading.yaml`, and the UI marks it.
3. **The LLM never grades flight skills.** Flight S/U is deterministic; the LLM may only advise on borderline legs and grade the (advisory) oral.
4. **Nothing in the per-second loop waits on the LLM.**
5. **Never implies FAA credit.** Every ACS-labelled output carries the practice-only footer.
6. **One edition at a time**, pinned by checksum; a new ACS edition is a data migration (re-extract, re-review bindings), not a code change.
