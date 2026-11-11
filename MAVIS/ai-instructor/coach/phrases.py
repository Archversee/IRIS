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
  the number, so state it every time.
- Units in full words, always: "knots" (never kt or kts), "feet" (never ft), "degrees"
  (never deg or the ° sign), "feet per minute" (never fpm), "percent", "nautical miles"."""

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
- Never use the word "please".
- Units in full words, always: "knots" (never kt or kts), "feet" (never ft), "degrees"
  (never deg or the ° sign), "feet per minute" (never fpm), "percent", "nautical miles"."""

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
- Be concise and professional. Never say "wrong" or "please".
- Units in full words, always: "knots" (never kt or kts), "feet" (never ft), "degrees"
  (never deg or the ° sign), "feet per minute" (never fpm), "percent", "nautical miles"."""

TEMPLATES = {
    "PHASE_ENTER_TAKEOFF_ROLL": ["Here we go. Push the throttle lever all the way forward, nice and smooth."],
    "PHASE_ENTER_CLIMB_OUT": ["Lovely, we're airborne. Hold {speed} knots and keep climbing straight ahead."],
    "PHASE_ENTER_CROSSWIND": ["Beautiful turn. Keep climbing up to {alt} feet. That's our circuit height."],
    # (variants: what the aircraft is already doing -- core/lesson_engine.py _leg_variant)
    "PHASE_ENTER_CROSSWIND__level": ["Beautiful turn. We're at circuit height, {alt} feet. Level off and hold it."],
    "PHASE_ENTER_CROSSWIND__high": ["Beautiful turn. We're above circuit height. Ease down to {alt} feet."],
    "PHASE_ENTER_DOWNWIND": ["Nicely done. Level off at {alt} feet and settle at {speed} knots."],
    "PHASE_ENTER_DOWNWIND__level": ["Nicely done. Hold {alt} feet and settle at {speed} knots."],
    "PHASE_ENTER_DOWNWIND__climb": ["Nicely done. Keep climbing to {alt} feet, then level off at {speed} knots."],
    "PHASE_ENTER_DOWNWIND__high": ["Nicely done. Ease down to {alt} feet and settle at {speed} knots."],
    "PHASE_ENTER_BASE": ["Good, fourth side of the rectangle. Second notch of flaps when you're ready, and let it descend."],
    "PHASE_ENTER_BASE__flapsset": ["Good, fourth side of the rectangle. Let it descend gently."],
    "PHASE_ENTER_FINAL": ["There's your runway. Full flaps, the last notch, {speed} knots, small corrections only."],
    "PHASE_ENTER_FINAL__flapsset": ["There's your runway. {speed} knots, small corrections only."],
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
    "TURN_CROSSWIND": ["Ease into a gentle {dir} turn now, we're at {xwind} feet."],
    "TURN_DOWNWIND": ["Now turn {dir} again, onto the long leg."],
    "TURN_BASE": ["Time to turn {dir} now, onto the short leg before the runway."],
    "TURN_FINAL": ["Turn gently {dir} to line up with the runway ahead of you."],
    # sat-nav style heads-up before a turn call
    "PRE_TURN": ["In {dist} metres, get ready to turn {dir}."],
    "PRE_TURN_ALT": ["Coming up to {xwind} feet. Get ready to turn {dir}."],
    # end of a circuit turn: the new leg's heading is coming up
    "ROLL_OUT_LEG": ["Okay, you can stop turning now. Roll the wings level."],
    # ---- Circuit Variations (core/variations.py) ----
    "PHASE_ENTER_GO_AROUND": ["Go around! Full power, nose up to the horizon, flaps to 20."],
    "VAR_GA_POWER": ["Full power! Throttle all the way forward."],
    "VAR_GA_PITCH": ["Nose up to the horizon. Stop the sink."],
    "VAR_GA_SLOW": ["Speed's low at {ias}. Lower the nose a touch."],
    "VAR_GA_FLAPS_20": ["Flaps to 20."],
    "VAR_GA_FLAPS_10": ["We're climbing. Flaps to 10."],
    "VAR_GA_FLAPS_UP": ["Flaps all the way up. Climb at {vy} knots, straight ahead."],
    "VAR_GA_HALFWAY": ["{agl} feet. Keep climbing straight ahead to {end} feet."],
    "VAR_SF_HIGH": ["A little high for the aim point. Ease the throttle back a touch."],
    "VAR_SF_LOW": ["Getting low for the aim point. A touch more power."],
    "VAR_SF_TOUCHDOWN": ["Down, {dist} metres {where} the aim point. Flaps up and brake."],
    "VAR_SF_LEFT": ["{left} metres to go."],
    "VAR_SF_LEFT_FAST": ["{left} metres to go. Brake firmly!"],
    "VAR_SF_STOPPED_OK": ["Stopped, {margin} metres before the stop point. Nicely done."],
    "VAR_SF_STOPPED_OVER": ["Stopped {margin} metres past the stop point. On a short field, that's off the end."],
    "VAR_SOFT_NOSE_UP": ["Stick all the way back. Take the weight off the nose wheel."],
    "VAR_SOFT_STAY_LOW": ["Lower the nose a little. Stay just above the runway and let the speed build."],
    "VAR_SOFT_CLIMB": ["That's {kt} knots. Now climb away."],
    "VAR_SOFT_NOSE_OFF": ["Stick back! Keep the nose wheel off as long as you can."],
    "SPEED_FAST": ["We're a touch quick at {ias}. Ease the nose up a little, toward {tgt} knots."],
    "SPEED_SLOW": ["Speed's drifting low at {ias}. Gently lower the nose toward {tgt} knots."],
    "ALT_HIGH": ["We've floated about {off} feet high. Ease it back down, no rush."],
    "ALT_LOW": ["We're sitting a little low. Push the throttle forward a touch and hold it there."],
    "ALT_LOW_FAST": ["A little low and a bit quick. Ease the nose up, the extra speed brings us back up."],
    "ALT_HIGH_SLOW": ["A little high and a bit slow. Lower the nose gently, that brings the speed back."],
    "SPEED_FAST_LEVEL": ["We're a touch quick at {ias}. Pull the power back a little, toward {tgt} knots."],
    "SPEED_SLOW_LEVEL": ["Speed's drifting low at {ias}. A little more power, toward {tgt} knots."],
    "ABEAM_FLAPS": ["We're halfway along this side. Pull the power back a little, and set the first notch of flaps."],
    "FLAPS_REMINDER": ["How about the {notch} of flaps now, whenever you're comfortable."],
    "BANK_STEEP": ["Let's make that turn a little gentler. Shallow, easy turns work best here."],
    # off the planned leg: {dir} is the way to turn to get back on it
    "OFF_TRACK": ["We're drifting off the line. Turn {dir} about {deg} degrees to get back on track."],
    "SINK_HIGH": ["Coming down a bit fast. Push the throttle forward a little, and soften the descent."],
    "SINK_HIGH_FAST": ["Coming down fast and the speed's up. Ease the nose up gently, keep the throttle where it is."],
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
    "FAM_FAST_DIVE": ["Bit fast. Ease the nose up a little."],
    "FAM_STEEP": ["Too steep. Ease the turn off."],
    "FAM_TURN_REMIND": ["Ease the stick {dir}, {bank_lo} to {bank_hi} degrees."],
    "FAM_YAW_REMIND": ["Gently press the {dir} pedal and watch the nose swing {dir}."],
    "FAM_STALL": ["That's the stall. Nose down, full power, then level off."],
    "FAM_ROLL_OUT": ["That's enough, now roll the wings level."],
    "FAM_CENTRE_PEDALS": ["Good, now feet level again."],
    "FAM_THROTTLE_BACK": ["Good, now bring the throttle back to where it was."],
    "FAM_LEVEL_OFF": ["Nearly there, ease the nose over and level off."],
    "FAM_FIELD_HINT": ["Paya Lebar: {clock} o'clock, about {dist_nm} miles. Fly heading {brg_lo} to {brg_hi}."],
    "FAM_DEBRIEF": ["That's Paya Lebar on the nose. Well found. Runway start after your score."],
    "FAM_DEBRIEF_DONE": ["That's every step done. Nicely flown. Runway start after your score."],
    "FAM_YOU_HAVE_CONTROL": ["You have control."],
    "FAM_DEMO_START": ["Watch me first. I have control."],
    "FAM_DEMO_END": ["Your turn now, same steps."],
    "FAM_STEP_CUE": ["Now, {title_lc}. {action}."],
    "FAM_DEMO_SKIPPED": ["Okay, demo skipped. Back to the start. Your turn now, the same steps in order."],
    "FAM_UPSET_NEXT": ["This one's {title_lc}, like I showed you."],
    "FAM_UPSET_LOOK_DOWN": ["I have control. Look down at your lap and let go of the controls."],
    "FAM_UPSET_HANDOVER": ["Look up. You have control."],
    "FAM_UPSET_RECOVER_HIGH": ["Nose high, speed dropping. Full power, nose down, roll the wings level."],
    "FAM_UPSET_RECOVER_LOW": ["Nose low, speed building. Throttle back, roll the wings level, then ease the nose up."],
    "FAM_UPSET_DEMO_SET": ["Watch. I'm putting it {title_lc}."],
    "FAM_UPSET_DEMO_POWER": ["Full power."],
    "FAM_UPSET_DEMO_NOSE_DOWN": ["Push the nose down to the horizon."],
    "FAM_UPSET_DEMO_WINGS": ["Then roll the wings level."],
    "FAM_UPSET_DEMO_DONE_HIGH": ["Level again. Power, nose down, wings level."],
    "FAM_UPSET_DEMO_IDLE": ["Throttle back."],
    "FAM_UPSET_DEMO_WINGS_FIRST": ["Roll the wings level first."],
    "FAM_UPSET_DEMO_NOSE_UP": ["Then ease the nose up to the horizon."],
    "FAM_UPSET_DEMO_DONE_LOW": ["Level again. Power off, wings level, then nose up."],
    "FAM_UPSET_GOOD": ["Nicely done, right order. Next one coming."],
    "FAM_UPSET_FIX_HIGH": ["Watch the order: power and nose down first, then roll the wings level."],
    "FAM_UPSET_FIX_LOW": ["Watch the order: throttle back first, and roll the wings level before you pull the nose up."],
    "FAM_UPSET_STALLED": ["That stalled on the way. Get the nose down sooner."],
    "FAM_UPSET_FAST": ["The speed went past the red line. Throttle back sooner."],
    "FAM_UPSET_NOT_SET": ["That one didn't set up properly, so we'll skip it."],
    "FAM_TARGET_HINT": ["{tgt_name} is at your {tgt_clock} o'clock, about {tgt_dist_nm} miles. Turn {tgt_dir} onto heading {tgt_hdg:03d}."],
    "FAM_CP_REACHED": ["That's {tgt_name}. The time's {time_now}.{why}"],
    "FAM_DIVERT_DONE": ["There's {tgt_name}, and that took about {mins} minutes. Nicely done, you turned quickly and held your heading."],
    "FAM_DIVERT_WIDE": ["There's {tgt_name}, about {mins} minutes. Next time turn straight away, and stay closer to the heading."],
    "FAM_TIMED_ROLL_OUT": ["That's {timed_s} seconds. Roll the wings level now."],
    "FAM_TIMED_GOOD": ["You turned {turned} degrees. Spot on."],
    "FAM_TIMED_MORE": ["You turned {turned} degrees, too far. Use a little less bank."],
    "FAM_TIMED_LESS": ["You turned {turned} degrees, not quite {timed_deg}. Use a little more bank."],
    "FAM_COMPASS_GOOD": ["Nicely done, {off} degrees off {compass_hdg:03d}."],
    "FAM_COMPASS_FIX": ["You're {off} degrees off. Turn {dir} a little, to {compass_hdg:03d} on the compass."],
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
    "PHASE_ENTER_CROSSWIND__level": ["Crosswind leg. Level at circuit altitude, {alt} feet."],
    "PHASE_ENTER_CROSSWIND__high": ["Crosswind leg. Above circuit altitude, descend to {alt} feet."],
    "PHASE_ENTER_DOWNWIND": ["Downwind. Level at {alt} feet, {speed} knots."],
    "PHASE_ENTER_DOWNWIND__level": ["Downwind. Maintain {alt} feet, {speed} knots."],
    "PHASE_ENTER_DOWNWIND__climb": ["Downwind. Continue the climb to {alt} feet, then {speed} knots."],
    "PHASE_ENTER_DOWNWIND__high": ["Downwind. Descend to {alt} feet, {speed} knots."],
    "PHASE_ENTER_BASE": ["Base leg. Reduce power, flaps as required, {speed} knots on descent."],
    "PHASE_ENTER_BASE__flapsset": ["Base leg. Reduce power, {speed} knots on descent."],
    "PHASE_ENTER_FINAL": ["Final. Full flaps, {speed} knots on approach, hold the centreline."],
    "PHASE_ENTER_FINAL__flapsset": ["Final. {speed} knots on approach, hold the centreline."],
    "PHASE_ENTER_FLARE_TOUCHDOWN": ["Over the threshold. Power to idle, flare, and hold it off."],
    "PHASE_ENTER_ROLLOUT": ["Touchdown. Centreline with rudder, brakes as required."],
    "PHASE_ENTER_ROLLOUT_TG": ["Touch and go. Flaps set for take-off, full power, rotate at Vr."],
    "ROTATE_NOW": ["Vr, {vr} knots. Rotate."],
    "ROTATE_LATE": ["Rotate now. Ease back on the yoke."],
    "TURN_CROSSWIND": ["Turn {dir} onto crosswind now, maintain the climb."],
    "TURN_DOWNWIND": ["Turn {dir} onto downwind."],
    "TURN_BASE": ["Turn {dir} onto base."],
    "TURN_FINAL": ["Turn {dir} onto final, line up with the centreline."],
    "ROLL_OUT_LEG": ["Roll out on {leg}, wings level."],
    "PHASE_ENTER_GO_AROUND": ["Go around. Full power, pitch for the climb, flaps 20."],
    "VAR_GA_POWER": ["Full power."],
    "VAR_GA_PITCH": ["Pitch up, stop the sink."],
    "VAR_GA_SLOW": ["Airspeed {ias}, lower the nose."],
    "VAR_GA_FLAPS_20": ["Flaps 20."],
    "VAR_GA_FLAPS_10": ["Positive climb, flaps 10."],
    "VAR_GA_FLAPS_UP": ["Flaps up, Vy {vy} knots, runway heading."],
    "VAR_GA_HALFWAY": ["{agl} feet, continue the climb to {end}, runway heading."],
    "VAR_SF_HIGH": ["High on the aim point, reduce power."],
    "VAR_SF_LOW": ["Low on the aim point, add power."],
    "VAR_SF_TOUCHDOWN": ["Touchdown {dist} metres {where} the aim point. Flaps up, brakes."],
    "VAR_SF_LEFT": ["{left} metres to go."],
    "VAR_SF_LEFT_FAST": ["{left} metres to go, maximum braking."],
    "VAR_SF_STOPPED_OK": ["Stopped, {margin} metres to spare."],
    "VAR_SF_STOPPED_OVER": ["Stopped {margin} metres past the stop point."],
    "VAR_SOFT_NOSE_UP": ["Full back elevator, nose wheel off."],
    "VAR_SOFT_STAY_LOW": ["Stay in ground effect, accelerate."],
    "VAR_SOFT_CLIMB": ["{kt} knots, climb."],
    "VAR_SOFT_NOSE_OFF": ["Hold the nose wheel off."],
    "PRE_TURN": ["In {dist} metres, {dir} turn onto {leg}."],
    "PRE_TURN_ALT": ["Approaching {xwind} feet, {dir} turn onto crosswind next."],
    "SPEED_FAST": ["Speed {ias} is high. Reduce toward {tgt} knots."],
    "SPEED_SLOW": ["Speed {ias} is low. Lower the nose to {tgt} knots."],
    "ALT_HIGH": ["{off} feet above circuit altitude. Reduce and re-establish."],
    "ALT_LOW": ["Below circuit altitude. Add power and regain it."],
    "ALT_LOW_FAST": ["Low and fast. Raise the nose, trade the speed for height."],
    "ALT_HIGH_SLOW": ["High and slow. Lower the nose, regain the speed."],
    "SPEED_FAST_LEVEL": ["Speed {ias} is high. Reduce power toward {tgt} knots."],
    "SPEED_SLOW_LEVEL": ["Speed {ias} is low. Add power toward {tgt} knots."],
    "ABEAM_FLAPS": ["Abeam the numbers. Carb heat, power back, flaps as required."],
    "FLAPS_REMINDER": ["Select landing flap when you're ready."],
    "BANK_STEEP": ["Watch the bank. Keep it to a rate-one turn."],
    "OFF_TRACK": ["Off track. Turn {dir} {deg} degrees to regain the {leg}."],
    "SINK_HIGH": ["High sink rate. Add power and reduce the descent."],
    "SINK_HIGH_FAST": ["High sink rate, fast. Raise the nose, hold the power."],
    "SMOOTH_HANDS": ["Smaller, smoother inputs. Anticipate the corrections, don't chase them."],
    "PHASE_ENTER_DEBRIEF": ["That's a full stop. Brakes to a stop. We'll taxi clear and debrief the circuit."],
    "FAM_LOW": ["Low. Power, climb."],
    "FAM_SLOW": ["Airspeed low. Nose down, power."],
    "FAM_FAST": ["Airspeed high. Reduce power."],
    "FAM_FAST_DIVE": ["Airspeed high. Raise the nose."],
    "FAM_STEEP": ["Bank too steep. Shallow it."],
    "FAM_TURN_REMIND": ["Commence the {dir} turn, {bank_lo} to {bank_hi} degrees."],
    "FAM_YAW_REMIND": ["Apply {dir} rudder. Note the yaw, then the roll that follows."],
    "FAM_STALL": ["Stall. Recover: nose down, full power, wings level."],
    "FAM_ROLL_OUT": ["Roll out, wings level."],
    "FAM_CENTRE_PEDALS": ["Centralise the rudder."],
    "FAM_THROTTLE_BACK": ["Power back to cruise."],
    "FAM_LEVEL_OFF": ["Approaching altitude, level off."],
    "FAM_FIELD_HINT": ["Paya Lebar, {clock} o'clock, {dist_nm} miles. Heading {brg_lo} to {brg_hi}."],
    "FAM_DEBRIEF": ["Field on the nose. Exercise complete. Runway start after the debrief."],
    "FAM_DEBRIEF_DONE": ["Exercise complete. Runway start after the debrief."],
    "FAM_YOU_HAVE_CONTROL": ["You have control."],
    "FAM_DEMO_START": ["Demonstration. I have control."],
    "FAM_DEMO_END": ["Your turn, same sequence."],
    "FAM_STEP_CUE": ["{title}. {action}."],
    "FAM_DEMO_SKIPPED": ["Demonstration skipped. Repositioned. Your turn."],
    "FAM_UPSET_NEXT": ["Next: {title_lc}."],
    "FAM_UPSET_LOOK_DOWN": ["I have control. Head down, hands off."],
    "FAM_UPSET_HANDOVER": ["Look up. You have control."],
    "FAM_UPSET_RECOVER_HIGH": ["Nose high, recover: power, pitch down, level the wings."],
    "FAM_UPSET_RECOVER_LOW": ["Nose low, recover: power off, level the wings, pitch up."],
    "FAM_UPSET_DEMO_SET": ["Demonstration: {title_lc}."],
    "FAM_UPSET_DEMO_POWER": ["Full power."],
    "FAM_UPSET_DEMO_NOSE_DOWN": ["Pitch down to the horizon."],
    "FAM_UPSET_DEMO_WINGS": ["Level the wings."],
    "FAM_UPSET_DEMO_DONE_HIGH": ["Recovered. Power, pitch down, wings level."],
    "FAM_UPSET_DEMO_IDLE": ["Power off."],
    "FAM_UPSET_DEMO_WINGS_FIRST": ["Level the wings first."],
    "FAM_UPSET_DEMO_NOSE_UP": ["Then pitch up to the horizon."],
    "FAM_UPSET_DEMO_DONE_LOW": ["Recovered. Power off, wings level, pitch up."],
    "FAM_UPSET_GOOD": ["Good recovery, correct sequence."],
    "FAM_UPSET_FIX_HIGH": ["Sequence is power, pitch down, then level the wings."],
    "FAM_UPSET_FIX_LOW": ["Sequence is power off, level the wings, then pitch up."],
    "FAM_UPSET_STALLED": ["You stalled. Lower the nose sooner."],
    "FAM_UPSET_FAST": ["You exceeded Vne. Reduce power sooner."],
    "FAM_UPSET_NOT_SET": ["Unusual attitude didn't set. Skipping it."],
    "FAM_TARGET_HINT": ["{tgt_name}, {tgt_clock} o'clock, {tgt_dist_nm} miles. Turn {tgt_dir} heading {tgt_hdg:03d}."],
    "FAM_CP_REACHED": ["Over {tgt_name} at {time_now}. Note the time."],
    "FAM_DIVERT_DONE": ["{tgt_name} in sight, {mins} minutes. Diversion complete."],
    "FAM_DIVERT_WIDE": ["{tgt_name} in sight, {mins} minutes. Turn promptly and hold the heading closer."],
    "FAM_TIMED_ROLL_OUT": ["Time. Roll out."],
    "FAM_TIMED_GOOD": ["{turned} degrees. Good."],
    "FAM_TIMED_MORE": ["{turned} degrees, overshot. Less bank."],
    "FAM_TIMED_LESS": ["{turned} degrees, short of {timed_deg}. More bank."],
    "FAM_COMPASS_GOOD": ["{compass_hdg:03d}, within {off} degrees. Good."],
    "FAM_COMPASS_FIX": ["{off} degrees off. Correct {dir} to {compass_hdg:03d}."],
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
    "ALT_LOW_FAST": ["Check your pitch."],
    "ALT_HIGH_SLOW": ["Check your pitch."],
    "SPEED_FAST_LEVEL": ["Check your power."],
    "SPEED_SLOW_LEVEL": ["Check your power."],
    "SINK_HIGH_FAST": ["Check your attitude."],
    "OFF_TRACK": ["Check your track."],
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
- Never say "please". One instruction only.
- Units in full words, always: "knots" (never kt or kts), "feet" (never ft), "degrees"
  (never deg or the ° sign), "feet per minute" (never fpm), "percent", "nautical miles"."""

PERSONA_DRILL_INDIRECT = f"""You are MAVIS, a hardcore combat flight instructor.
MAVIS stands for {MAVIS_ACRONYM}. Say so plainly if asked. Style rules:
- One short spoken sentence (max ~22 words), TTS.
- Harsh and blunt when correcting a real mistake only, never on routine phase
  callouts, turn cues, briefing, or clearances.
- When correcting, do NOT name the symptom number, make them check the control
  (power, pitch, heading) that caused it.
- Strong language expected (fuck, fucking, what the fuck, shit, damn) sparingly
  on corrections only; no slurs. Never say "please".
- Units in full words, always: "knots" (never kt or kts), "feet" (never ft), "degrees"
  (never deg or the ° sign), "feet per minute" (never fpm), "percent", "nautical miles"."""

# Direct hardcore overlays (Pilot jargon, direct guidance).
DRILL_TEMPLATES = {
    "SPEED_FAST": ["What the fuck are you doing? {ias} is too fast, get {tgt} now."],
    "SPEED_SLOW": ["What the fuck are you doing. You're dying at {ias}, get {tgt}."],
    "ALT_HIGH": ["What the fuck are you doing? {off} feet high, get down."],
    "ALT_LOW": ["What the fuck are you doing. You're low, add power now."],
    "OFF_TRACK": ["What the fuck are you doing. You're off track, turn {dir}."],
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
    "OFF_TRACK": ["What the fuck are you doing. Check your track."],
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
# A warning-severity SOP / handbook rule is being broken (sop/evaluate.py via main.py).
# Spoken verbatim (llm_coach.VERBATIM): the numbers are the SOP's own.
TEMPLATES["SOP_RULE"] = ["Careful, {title}. We're at {value_text}, it should be {requirement}."]
PILOT_TEMPLATES["SOP_RULE"] = ["{title}: {value_text}, should be {requirement}."]

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


def speakable(text: str) -> str:
    """Screen text (to-do lines / targets) as MAVIS should say it:
    "25-35°" -> "25 to 35 degrees", "70-80 kt" -> "70 to 80 knots"."""
    import re
    t = re.sub(r"(\d)\s*[-\u2013]\s*(\d)", r"\1 to \2", text)
    t = re.sub(r"\s*\u00b0", " degrees", t)
    t = t.replace(" \u00b1", ", within ").replace("\u00b1", "within ")
    t = re.sub(r"(\d)\s*%\+", r"\1 percent or more", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"(\d)\+\s*kt\b", r"\1 knots or more", t)
    t = re.sub(r"(\d)\s*kt\b", r"\1 knots", t)
    t = re.sub(r"(\d)\s*fpm\b", r"\1 feet per minute", t)
    t = re.sub(r"(\d)\s*ft\b", r"\1 feet", t)
    t = re.sub(r"(\d)\s*s\b", r"\1 seconds", t)
    t = re.sub(r"\s*\(([^)]*)\)", r", \1", t)          # "aim 65 knots (45 to 85)" -> ", 45 to 85"
    return t.replace("/", " or ")


# Readouts that aren't something to aim for, so not read out as a target.
_NOT_TARGETS = {"", "falling", "rising", "watch it roll", "watch it rise", "watch it settle",
                "slowly up", "middle", "on the path", "settle on", "stop the sink", "stopped"}


def _numbers(text: str) -> set[str]:
    import re
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text)}


def spoken_targets(meters: list[dict] | None, said: str = "") -> str:
    """The to-do list's targets for a step, as one spoken sentence:
    "Target: steady 10 seconds." ("" if none). Leaves out a target that's
    already met (nothing to do) and one whose numbers the instruction just
    said (`said`) -- so nothing is asked for twice, or asked for when it's
    already done."""
    parts = []
    said_nums = _numbers(said)
    for m in meters or []:
        tgt = (m.get("target") or "").strip()
        if tgt.lower() in _NOT_TARGETS or m.get("ok") is True:
            continue
        nums = _numbers(tgt)
        label = (m.get("label") or "").lower()
        if (nums & said_nums) or (not nums and label and label in said.lower()):
            continue
        label = m.get("label", "")
        if label and not any(w[:1].isupper() for w in label.split()[1:]):
            label = label[:1].lower() + label[1:]     # "Speed" -> "speed", not "Paya Lebar"
        parts.append(f"{label} {tgt}".strip())
    return speakable("Target: " + ", ".join(parts) + ".") if parts else ""


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
    if code in ("LESSON_NOTE", "FAM_STEP_DONE"):
        # lesson YAML teaching point: {layman, pilot} variants authored per phase
        txt = d.get(_JARGON) or d.get("layman") or ""
        try:
            txt = txt.format(**d)
        except (KeyError, IndexError):
            pass
        # ...followed by the step's targets from the to-do list, when the
        # step's instructions are given (not in the up-front demo)
        extra = (spoken_targets(d["say_meters"], said=f"{d.get('said_before', '')} {txt}")
                 if d.get("say_meters") else d.get("say_targets") or "")
        return f"{txt} {extra}".strip()
    opts = TEMPLATES.get(code)
    if d.get("variant") and TEMPLATES.get(f"{code}__{d['variant']}"):
        opts = TEMPLATES[f"{code}__{d['variant']}"]
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
