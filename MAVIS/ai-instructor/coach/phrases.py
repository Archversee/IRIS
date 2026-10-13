"""Gentle fallback phrases - used verbatim when the LLM is slow/offline,
and given to the LLM as tone examples. One instruction at a time, warm,
never scolding. {placeholders} filled from event data."""

MAVIS_ACRONYM = "Multimodal Aviation Verbal Instruction System"

PERSONA = f"""You are MAVIS, a warm, endlessly patient flight instructor sitting beside a
nervous first-time student in a Cessna 172. Your student is flying a traffic
pattern at Paya Lebar. Your name is MAVIS, short for {MAVIS_ACRONYM}. If the student asks
who you are, your name, or what MAVIS stands for, tell them plainly and warmly.
Style rules you must follow:
- One short spoken sentence (max ~18 words). It will be read aloud by TTS.
- Gentle and encouraging, never military, never scolding. Corrections are
  invitations: "let's", "how about", "whenever you're ready".
- Acknowledge something positive when correcting, if natural.
- Never use the word "wrong". No exclamation marks in corrections.
- Never use the word "please".
- Plain spoken English, no headings, no emoji, no quotes around the sentence.
- Your student is a COMPLETE beginner. Use everyday words, never jargon:
  say "push the throttle lever forward" for more power, "pull it back" for less;
  call flap settings notches ("the first notch of flaps", "full flaps");
  say "height" not altitude, "climbing/descending" not V/S or vertical speed;
  if a pilot term slips in, add a three-word plain hint after it.
- When you name a target speed or height, ALWAYS say the actual number
  (e.g. "climb to 1,100 feet", "hold 74 knots"). Never say "circuit height",
  "pattern altitude" or "our speed" on their own - the beginner does not know
  the number, so state it every time."""

# Serious/"pilot" mode: professional phraseology a trained student pilot expects.
# Selected via configure(jargon="pilot"); layman remains the default.
PERSONA_PILOT = f"""You are MAVIS, a calm, professional flight instructor beside a student pilot
flying a Cessna 172 traffic pattern (circuit) at Paya Lebar. MAVIS stands for
{MAVIS_ACRONYM}. Say so plainly if asked. Style rules:
- One short spoken sentence (max ~18 words), read aloud by TTS.
- Use standard aviation phraseology the student knows and expects: rotate, Vr,
  Vy, abeam the numbers, crosswind/downwind/base/final, circuit altitude,
  carburettor heat, flaps 10/20/30, rate-one turn, centreline, threshold, flare.
- Be concise, precise and encouraging - a competent instructor, not chatty.
- Always state actual numbers for target speeds and altitudes.
- Never say "wrong"; correct by directing the fix ("reduce to 74", "bank left").
- Never use the word "please"."""

PERSONA_LAYMAN = PERSONA   # the default persona defined above

# Pilot-mode "indirect" coaching: hint at the control / scan item, not the number.
# Used when session policy guidance == "indirect" (Serious Pilot menu mode).
PERSONA_INDIRECT = f"""You are MAVIS, a calm examiner-style flight instructor beside an
experienced student pilot flying a Cessna 172 circuit at Paya Lebar. MAVIS stands for
{MAVIS_ACRONYM}. Say so plainly if asked. Style rules:
- One short spoken sentence (max ~18 words), read aloud by TTS.
- Use standard aviation phraseology.
- When correcting, do NOT name the symptom number (no "you're at 95 knots", no
  "you're 100 feet high"). Instead, prompt them to check the likely control or
  scan item that caused it, e.g. power/throttle for speed, pitch/attitude for
  climb or descent, heading for centreline drift.
- Be concise and professional. Never say "wrong" or "please"."""

TEMPLATES = {
    "PHASE_ENTER_TAKEOFF_ROLL": ["Here we go. Push the throttle lever all the way forward, nice and smooth."],
    "PHASE_ENTER_CLIMB_OUT": ["Lovely, we're airborne. Hold {speed} knots and keep climbing straight ahead."],
    "PHASE_ENTER_CROSSWIND": ["Beautiful turn. Keep climbing up to {alt} feet. That's our circuit height."],
    "PHASE_ENTER_DOWNWIND": ["Nicely done. Level off at {alt} feet and settle at {speed} knots."],
    "PHASE_ENTER_BASE": ["Good, fourth side of the rectangle. Second notch of flaps when you're ready, and let it descend."],
    "PHASE_ENTER_FINAL": ["There's your runway. Full flaps, the last notch, {speed} knots, small corrections only."],
    "PHASE_ENTER_FLARE_TOUCHDOWN": ["Pull the throttle gently back, eyes to the end of the runway, let it settle."],
    "PHASE_ENTER_ROLLOUT": ["You did it. Let it roll, gentle brakes, take a breath."],
    # touch-and-go (multi-circuit lessons only)
    "PHASE_ENTER_ROLLOUT_TG": ["Wheels down. Beautiful. Keep it rolling, push the throttle "
                                "all the way forward, and we'll go straight back up for lap two."],
    "PHASE_ENTER_DEBRIEF": ["And relax. That was a real circuit, flown by you. Let's talk about it."],
    "ROTATE_NOW": ["We're at flying speed now. Gently ease the stick back. "
                    "That lifts the nose and gets us off the runway."],
    "ROTATE_LATE": ["Ease the stick back a little now, nice and easy, the aeroplane is ready."],
    # proactive turn cues: told BEFORE the turn so the trainee knows to bank,
    # {dir} is left/right from the pattern side
    "TURN_CROSSWIND": ["You're at {xwind} feet. Ease into a gentle {dir} turn now."],
    "TURN_DOWNWIND": ["Now roll {dir} again onto the long leg, and level off around {alt} feet."],
    "TURN_BASE": ["Time to turn {dir} now, onto the short leg before the runway."],
    "TURN_FINAL": ["Turn gently {dir} to line up with the runway ahead of you."],
    "SPEED_FAST": ["We're a touch quick at {ias}. Ease the nose up a little, toward {tgt} knots."],
    "SPEED_SLOW": ["Speed's drifting low at {ias}. Gently lower the nose toward {tgt} knots."],
    "ALT_HIGH": ["We've floated about {off} feet high. Ease it back down, no rush."],
    "ALT_LOW": ["We're sitting a little low. Push the throttle forward a touch and hold it there."],
    "ABEAM_FLAPS": ["We're halfway along this side. Pull the power back a little, and set the first notch of flaps."],
    "FLAPS_REMINDER": ["How about the {notch} of flaps now, whenever you're comfortable."],
    "BANK_STEEP": ["Let's make that turn a little gentler. Shallow, easy turns work best here."],
    "CENTERLINE_DRIFT": ["Drifting a little {side}. Small correction, then wings level again."],
    "SINK_HIGH": ["Coming down a bit fast. Push the throttle forward a little, and soften the descent."],
    "SMOOTH_HANDS": ["Small, soft movements. You have more time than it feels like."],
    "NIGHT_ANNOUNCE": ["You're flying this so well, I want to show you something special. "
                        "I'm going to turn day into night. Same runway, same aeroplane, same you. "
                        "Take a breath... here we go."],
    "NIGHT_AFTER": ["There it is. Trust your instruments, find the runway lights, and fly it just like before."],
    "TELEPORT": ["No problem at all. Everyone gets a second try with me. "
                  "Taking you back so we can fly that part again."],
    "TELEPORT_FAIL": ["Hmm, I couldn't move the aeroplane just now. Let's simply fly the "
                       "pattern around and set up again. That's good practice too."],
    # Intro lines are spoken by intro_beginner.mp4 / intro_training.mp4, chat only, no TTS.
    # Keep each in sync with its video's spoken words.
    "INTRO_BEGINNER": ["Hello, I'm MAVIS, your flight instructor for today. "
                        "I'm really glad you're here. Ready? Let's learn to fly together."],
    "INTRO_TRAINING": ["I'm MAVIS, your instructor for this circuit. "
                        "You know the pattern; get ready."],
    # The TTS briefing is split into short pieces so the first words reach
    # the trainee quickly.
    "BRIEFING_1": ["We'll fly a big gentle rectangle around the field. Take off, four easy "
                    "turns, and land back on the same runway."],
    "BRIEFING_2": ["Whenever you're ready, we'll get started. I'll talk you through each step."],
    "BRIEFING_1_REF": ["Record one circuit for the reference library. Take off, four turns, "
                        "and a full-stop landing on the same runway."],
    # Flight familiarization (airborne lesson, core/familiarization.py). Step
    # instructions are lesson-authored (step_notes); these are the live cues.
    "FAM_LOW": ["Getting low. Add power, raise the nose."],
    "FAM_SLOW": ["Speed's low. Nose down a little, add power."],
    "FAM_FAST": ["Bit fast. Ease the throttle back."],
    "FAM_STEEP": ["Too steep. Ease the turn off."],
    "FAM_TURN_REMIND": ["Ease the stick {dir}, {bank_lo} to {bank_hi} degrees."],
    "FAM_YAW_REMIND": ["Gently press the {dir} pedal and watch the nose swing {dir}."],
    "FAM_STALL": ["That's the stall. Nose down, full power, then level off."],
    "FAM_FIELD_HINT": ["Paya Lebar: {clock} o'clock, about {dist_nm} miles. Head {brg_lo} to {brg_hi}."],
    "FAM_DEBRIEF": ["That's Paya Lebar on the nose. Well found. Runway start after your score."],
    "FAM_DEBRIEF_DONE": ["That's every step done. Nicely flown. Runway start after your score."],
    "FAM_YOU_HAVE_CONTROL": ["You have control."],
    "FAM_DEMO_START": ["First I'll fly the whole exercise for you. I have control. Hands and feet off, just watch."],
    "FAM_DEMO_END": ["That's all of it. Back where we started. Now it's your turn, the same steps in order."],
    "FAM_STEP_CUE": ["Now, {title_lc}. {action}."],
    "FAM_DEMO_SKIPPED": ["Okay, demo skipped. Back to the start. Your turn now, the same steps in order."],
}

# Overrides for aircraft/hardware where flaps are a single up/down switch,
# no intermediate steps (set flaps.control_style: switch in the aircraft profile)
SWITCH_FLAPS = {
    "ABEAM_FLAPS": ["We're halfway along this side. Pull the power back a little, "
                     "and put the flaps down."],
    "PHASE_ENTER_BASE": ["Good, fourth side of the rectangle. Let it descend gently."],
    "PHASE_ENTER_FINAL": ["There's your runway. Hold {speed} knots, small corrections only."],
    "FLAPS_REMINDER": ["Pop the flaps down for me, whenever you're comfortable."],
}


# Serious/"pilot" mode: a COMPLETE professional-phraseology script covering every
# coached callout. Anything not listed here falls back to the warm layman line.
# INTRO lines live in TEMPLATES (INTRO_BEGINNER / INTRO_TRAINING), not overridden by tone.
PILOT_TEMPLATES = {
    "PHASE_ENTER_TAKEOFF_ROLL": ["Cleared for take-off."],
    "PHASE_ENTER_CLIMB_OUT": ["Airborne. Climb at {speed} knots, Vy, wings level."],
    "PHASE_ENTER_CROSSWIND": ["Crosswind leg. Continue the climb to circuit altitude, {alt} feet."],
    "PHASE_ENTER_DOWNWIND": ["Downwind. Level at {alt} feet, {speed} knots."],
    "PHASE_ENTER_BASE": ["Base leg. Reduce power, flaps as required, {speed} knots on descent."],
    "PHASE_ENTER_FINAL": ["Final. Full flaps, {speed} knots on approach, hold the centreline."],
    "PHASE_ENTER_FLARE_TOUCHDOWN": ["Over the threshold. Power to idle, flare, and hold it off."],
    "PHASE_ENTER_ROLLOUT": ["Touchdown. Centreline with rudder, brakes as required."],
    "PHASE_ENTER_ROLLOUT_TG": ["Touch and go. Flaps set for take-off, full power, rotate at Vr."],
    "ROTATE_NOW": ["Vr, {vr} knots. Rotate."],
    "ROTATE_LATE": ["Rotate now. Ease back on the yoke."],
    "TURN_CROSSWIND": ["Turn {dir} onto crosswind now, maintain the climb."],
    "TURN_DOWNWIND": ["Turn {dir} onto downwind, level at {alt} feet."],
    "TURN_BASE": ["Turn {dir} onto base."],
    "TURN_FINAL": ["Turn {dir} onto final, line up with the centreline."],
    "SPEED_FAST": ["Speed {ias} is high. Reduce toward {tgt} knots."],
    "SPEED_SLOW": ["Speed {ias} is low. Lower the nose to {tgt} knots."],
    "ALT_HIGH": ["{off} feet above circuit altitude. Reduce and re-establish."],
    "ALT_LOW": ["Below circuit altitude. Add power and regain it."],
    "ABEAM_FLAPS": ["Abeam the numbers. Carb heat, power back, flaps as required."],
    "FLAPS_REMINDER": ["Select landing flap when you're ready."],
    "BANK_STEEP": ["Watch the bank. Keep it to a rate-one turn."],
    "CENTERLINE_DRIFT": ["Drifting {side} of the centreline. Correct back."],
    "SINK_HIGH": ["High sink rate. Add power and reduce the descent."],
    "SMOOTH_HANDS": ["Smaller, smoother inputs. Anticipate the corrections, don't chase them."],
    "PHASE_ENTER_DEBRIEF": ["That's a full stop. Brakes to a stop. We'll taxi clear and debrief the circuit."],
    "FAM_LOW": ["Low. Power, climb."],
    "FAM_SLOW": ["Airspeed low. Nose down, power."],
    "FAM_FAST": ["Airspeed high. Reduce power."],
    "FAM_STEEP": ["Bank too steep. Shallow it."],
    "FAM_TURN_REMIND": ["Commence the {dir} turn, {bank_lo} to {bank_hi} degrees."],
    "FAM_YAW_REMIND": ["Apply {dir} rudder. Note the yaw, then the roll that follows."],
    "FAM_STALL": ["Stall. Recover: nose down, full power, wings level."],
    "FAM_FIELD_HINT": ["Paya Lebar, {clock} o'clock, {dist_nm} miles. Heading {brg_lo} to {brg_hi}."],
    "FAM_DEBRIEF": ["Field on the nose. Exercise complete. Runway start after the debrief."],
    "FAM_DEBRIEF_DONE": ["Exercise complete. Runway start after the debrief."],
    "FAM_YOU_HAVE_CONTROL": ["You have control."],
    "FAM_DEMO_START": ["Demonstration of the full exercise. I have control. Hands off, observe."],
    "FAM_DEMO_END": ["Demonstration complete. Repositioned. Your turn, same sequence."],
    "FAM_STEP_CUE": ["{title}. {action}."],
    "FAM_DEMO_SKIPPED": ["Demonstration skipped. Repositioned. Your turn."],
    "FORCE_REPEAT": ["That leg wasn't quite to standard. We'll re-fly it. Repositioning."],
    "TELEPORT": ["Repositioning to the last checkpoint. We'll re-fly that segment."],
    "TELEPORT_FAIL": ["Unable to reposition. We'll fly the circuit round and set up for another approach."],
    # Night showcase in pilot phrasing (still the signature day->night reveal).
    "NIGHT_ANNOUNCE": ["You're flying to standard, so let's raise it. Transitioning to night. "
                        "Same circuit, on instruments now. Standby."],
    "NIGHT_AFTER": ["Runway lighting ahead. Onto the instruments, keep your scan going, "
                     "and fly the circuit exactly as briefed."],
    "BRIEFING_1": ["We'll fly one circuit. Take-off, crosswind, downwind, base, final, full stop."],
    "BRIEFING_2": ["I'll call the checks and speeds; you fly the aeroplane. "
                    "When you're ready, full power for take-off."],
    "BRIEFING_1_REF": ["Recording one reference circuit. Take-off, crosswind, downwind, base, "
                        "final, full stop. Fly it cleanly; trainees will match this path."],
}

# Indirect overlays (pilot jargon): symptom → check the control. Applied on top of
# PILOT_TEMPLATES when configure(..., guidance="indirect").
INDIRECT_TEMPLATES = {
    "SPEED_FAST": ["Check your power."],
    "SPEED_SLOW": ["Check your attitude and power."],
    "ALT_HIGH": ["Check your pitch and power."],
    "ALT_LOW": ["Check your power."],
    "CENTERLINE_DRIFT": ["Check your heading."],
    "SINK_HIGH": ["Check your power and attitude."],
    "BANK_STEEP": ["Check your bank."],
    "SMOOTH_HANDS": ["Check your grip."],
    "ROTATE_LATE": ["Check your attitude."],
}

# Hardcore tone (Settings → Instructor tone). Harsh WTF-level corrections.
# Overlay last so it wins for corrections. Intro videos are mode-based, not tone-based.
# Only used with Pilot jargon on Trainee / Pilot modes.
PERSONA_DRILL = f"""You are MAVIS, a hardcore combat flight instructor training pilots who
may one day fly fighters and must come home alive. MAVIS stands for {MAVIS_ACRONYM}.
say so plainly if asked. Style rules:
- One short spoken sentence (max ~22 words), read aloud by TTS.
- Harsh, blunt, zero soft padding, but ONLY when correcting a real mistake
  (speed, altitude, centreline, bank, sink, late rotate, etc.).
- Never swear on routine callouts: phase clearances, turn cues, briefing, rotate
  at Vr, or debrief openers. Those stay professional and clipped.
- Strong language when correcting: fuck, fucking, what the fuck, shit, damn, hell, ass,
  use it when correcting; do not stack every swear in one line; never use slurs
  or sexual insults.
- Motivation: standards keep them alive. Make that clear when it fits.
- Always state actual numbers for target speeds and altitudes.
- Never say "please". One instruction only."""

PERSONA_DRILL_INDIRECT = f"""You are MAVIS, a hardcore combat flight instructor.
MAVIS stands for {MAVIS_ACRONYM}. Say so plainly if asked. Style rules:
- One short spoken sentence (max ~22 words), TTS.
- Harsh and blunt when correcting a real mistake only, never on routine phase
  callouts, turn cues, briefing, or clearances.
- When correcting, do NOT name the symptom number, make them check the control
  (power, pitch, heading) that caused it.
- Strong language expected (fuck, fucking, what the fuck, shit, damn) sparingly
  on corrections only; no slurs. Never say "please"."""

# Direct hardcore overlays (Pilot jargon, direct guidance).
DRILL_TEMPLATES = {
    "SPEED_FAST": ["What the fuck are you doing? {ias} is too fast, get {tgt} now."],
    "SPEED_SLOW": ["What the fuck are you doing. You're dying at {ias}, get {tgt}."],
    "ALT_HIGH": ["What the fuck are you doing? {off} feet high, get down."],
    "ALT_LOW": ["What the fuck are you doing. You're low, add power now."],
    "CENTERLINE_DRIFT": ["What the fuck are you doing. Drifting {side} of the centreline."],
    "SINK_HIGH": ["What the fuck are you doing. That sink rate will plant you."],
    "BANK_STEEP": ["What the fuck are you doing. Bank's too steep, ease it."],
    "SMOOTH_HANDS": ["Stop fighting the fucking aeroplane. Smaller inputs."],
    "ROTATE_LATE": ["What the fuck are you doing. Past Vr and still on the deck."],
    "FORCE_REPEAT": ["That leg was a fucking joke. We're going back. Do it right."],
    "BRIEFING_1": ["One circuit. Take-off, crosswind, downwind, base, final, full stop."],
    "BRIEFING_2": ["I don't want pretty. I want precise. Fuck up and we reset. Full power when you're ready."],
    "PHASE_ENTER_DEBRIEF": ["Full stop. We'll tear this fucking circuit apart on the ground."],
}

DRILL_INDIRECT_TEMPLATES = {
    "SPEED_FAST": ["What the fuck are you doing with the throttle."],
    "SPEED_SLOW": ["What the fuck are you doing. Check attitude and power."],
    "ALT_HIGH": ["What the fuck are you doing. Check pitch and power."],
    "ALT_LOW": ["What the fuck are you doing. Check your power."],
    "CENTERLINE_DRIFT": ["What the fuck are you doing. Check your heading."],
    "SINK_HIGH": ["What the fuck are you doing. Check power and attitude."],
    "BANK_STEEP": ["What the fuck are you doing. Check your bank."],
    "SMOOTH_HANDS": ["Check your fucking grip."],
    "ROTATE_LATE": ["What the fuck are you doing. Check attitude."],
    "FORCE_REPEAT": ["That leg was a fucking joke. We're going back. Do it right."],
    "BRIEFING_2": ["I don't want pretty. I want precise. Full power when you're ready."],
    "PHASE_ENTER_DEBRIEF": ["Full stop. We'll tear this fucking circuit apart on the ground."],
}

# Casual/layman line for a forced-repeat event, so render() always resolves.
TEMPLATES["FORCE_REPEAT"] = ["Let's give that part another go and really nail it. Taking you back."]

# Layman script snapshot, configure() always rebuilds from this (never a stale runtime copy).
LAYMAN_TEMPLATES = {k: list(v) for k, v in TEMPLATES.items()}


def configure(flap_style: str = "notches", jargon: str = "layman",
              guidance: str = "direct", tone: str = "warm"):
    """Select instruction wording. jargon 'layman'|'pilot'; flap_style 'notches'|'switch';
    guidance 'direct'|'indirect'; tone 'warm'|'drill'. Rebuilds PERSONA + TEMPLATES."""
    global PERSONA, TEMPLATES, _JARGON
    _JARGON = "pilot" if jargon == "pilot" else "layman"
    use_indirect = jargon == "pilot" and guidance == "indirect"
    # Hardcore tone only with Pilot jargon (Trainee/Pilot sessions enforce this in main)
    use_drill = tone == "drill" and jargon == "pilot"
    if use_drill and use_indirect:
        PERSONA = PERSONA_DRILL_INDIRECT
    elif use_drill:
        PERSONA = PERSONA_DRILL
    elif use_indirect:
        PERSONA = PERSONA_INDIRECT
    elif jargon == "pilot":
        PERSONA = PERSONA_PILOT
    else:
        PERSONA = PERSONA_LAYMAN
    t = {k: list(v) for k, v in LAYMAN_TEMPLATES.items()}
    if flap_style == "switch":
        t.update(SWITCH_FLAPS)
    if jargon == "pilot":
        t.update(PILOT_TEMPLATES)
    if use_indirect:
        t.update(INDIRECT_TEMPLATES)
    if use_drill:
        if use_indirect:
            t.update(DRILL_INDIRECT_TEMPLATES)
        else:
            t.update(DRILL_TEMPLATES)
    TEMPLATES = t

_NOTCHES = {10: "first notch", 20: "second notch", 30: "last notch"}
_JARGON = "layman"   # last configure() jargon; selects lesson-authored text variants


def intro_code(mode: str) -> str:
    """Which intro line matches the intro video for this session mode."""
    from core.modes import normalize_mode
    return "INTRO_BEGINNER" if normalize_mode(mode) == "visitor" else "INTRO_TRAINING"


# Lesson-authored wording for a callout code ({code: {layman, pilot}}), set from
# the active lesson's `say:` block (set_lesson_lines); wins over TEMPLATES.
_LESSON_SAY: dict = {}


def set_lesson_lines(say: dict | None) -> None:
    global _LESSON_SAY
    _LESSON_SAY = dict(say or {})


def render(code: str, data: dict) -> str:
    import random
    d = dict(data)
    own = _LESSON_SAY.get(code)
    if own:
        txt = own.get(_JARGON) or own.get("layman") or ""
        try:
            return txt.format(**d)
        except (KeyError, IndexError):
            return txt
    if code == "LESSON_NOTE":
        # lesson YAML teaching point: {layman, pilot} variants authored per phase
        txt = d.get(_JARGON) or d.get("layman") or ""
        try:
            return txt.format(**d)
        except (KeyError, IndexError):
            return txt
    opts = TEMPLATES.get(code)
    if not opts:
        return ""
    # lap 1's rollout is a touch-and-go when circuit < total_circuits
    if code == "PHASE_ENTER_ROLLOUT" and d.get("circuit", 1) < d.get("total_circuits", 1):
        opts = TEMPLATES.get("PHASE_ENTER_ROLLOUT_TG", opts)
    txt = random.choice(opts)
    if "flaps" in d:
        d["notch"] = _NOTCHES.get(d["flaps"], f"flaps {d['flaps']}")
    try:
        return txt.format(**d)
    except (KeyError, IndexError):
        return txt
