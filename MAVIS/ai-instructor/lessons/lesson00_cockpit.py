"""Cockpit Familiarisation: read-along checklist content.

Companion to lesson00_preflight.py, following phrases.py's spoken-content
convention: plain Python rather than YAML, since this is narration text and not
tunable config. See lessons/lesson00_cockpit_script_DRAFT.md for provenance.

Content is written for the older, round-gauge Cessna 172 shown in the 3D view
(web/static/models/cessna.glb), not the G1000 glass panel. The "pfd" and "mfd"
ids are kept from the G1000 version of the lesson (the 3D viewers key their
hotspots and cameras on them): "pfd" is now the flight instruments and "mfd" the
radio stack. The model's dials are not in the standard six-pack order, so the
narration says what each one reads rather than where it sits. Verify against the
aircraft's own POH before treating this as authoritative.

Note that flaps are described here as a lever with detents, which is what the
physical cockpit has. The aircraft profile's flaps.control_style: switch is a
separate concern: it shapes spoken phrasing during flight to match the HOTAS
hardware, not the cockpit being described in this lesson.
"""

TITLE = "Cockpit Familiarisation"

CHECKPOINTS = [
    {
        "id": "seating",
        "title": "Getting settled",
        "say": "Let's get familiar with the cockpit before we go anywhere. Get "
               "yourself seated and comfortable. Adjust the seat and rudder "
               "pedals until you can reach everything with your arms slightly "
               "bent and your feet flat on the pedals.",
        "details": [
            "Seatbelt and shoulder harness fastened before anything else.",
            "The yoke steers you in the air. Push or pull for the nose down and "
            "up, turn it to roll left and right.",
            "The rudder pedals steer you on the ground, and keep the nose "
            "straight in the air.",
        ],
    },
    {
        "id": "power_quadrant",
        "title": "Throttle and mixture",
        "say": "In the middle of the panel, two push-pull knobs control the engine. The "
               "black one is the throttle. Push it in for more power, pull it "
               "out for less. The red one is the mixture. Push it in to richen "
               "the fuel mix, pull it out to lean it.",
        "details": [
            "Mixture stays full rich for take-off and landing at low altitude.",
        ],
    },
    {
        "id": "ignition",
        "title": "Starting the engine",
        "say": "Down on the lower left of the panel is the ignition switch: "
               "Off, Right, Left, Both, and Start, same as a car key with an "
               "extra notch. Just above it, the red master switch comes in two "
               "halves: battery gives the aircraft electrical power, alternator "
               "keeps the battery charged once the engine's running.",
        "details": [
            "The ignition switch sits on Both once the engine is running.",
            "Battery and alternator switches are normally moved together.",
            "The knob to the left is the primer. It pumps a little fuel into the "
            "engine to help a cold start, and is locked back in afterwards.",
        ],
    },
    {
        "id": "fuel_system",
        "title": "Fuel system",
        "say": "Down on the floor between the seats, the fuel selector picks "
               "which tank feeds the engine: Left, Both, or Right. The fourth "
               "position, Off, cuts the fuel completely.",
        "details": [
            "Fuel selector on Both for take-off and landing.",
            "Left and Right are for level flight only.",
            "To reach Off you push the handle down and then turn it, so it can't "
            "be selected by accident.",
        ],
    },
    {
        "id": "pfd",
        "title": "The flight instruments",
        "say": "The round dials in front of you are the flight instruments, and "
               "each one tells you one thing. The airspeed indicator shows how "
               "fast you're going in knots. The attitude indicator, the blue and "
               "black one, shows your pitch and bank. The altimeter shows how "
               "high you are, the vertical speed indicator how quickly you're "
               "climbing or descending, and the heading indicator which way "
               "you're pointing.",
        "details": [
            "In most Cessna 172s the main six, including a turn coordinator, sit in "
            "two rows of three called the six-pack. The dials in this 3D model are "
            "placed differently, so go by what each one reads.",
            "The altimeter has to be set to the local pressure before you fly.",
            "The heading indicator drifts, so check it against the magnetic "
            "compass regularly.",
            "Don't stare at one dial. Keep your eyes moving, and come back to the "
            "attitude indicator each time.",
        ],
    },
    {
        "id": "mfd",
        "title": "The radio stack",
        "say": "In the middle of the panel is the radio stack, separate boxes one "
               "above the other. The audio panel is at the top and picks which "
               "radio you hear and talk on. Below it is the GPS, then two "
               "combined radios, each with a communication side for talking and "
               "a navigation side for beacons.",
        "details": [
            "Each radio shows two frequencies, the one in use and the one on "
            "standby. You set the standby, then swap them over.",
            "The box at the bottom with the numbered buttons is the transponder, "
            "which identifies you to air traffic control.",
        ],
    },
    {
        "id": "avionics",
        "title": "Avionics and electrical",
        "say": "Behind the yoke, the avionics master switch turns the radios and "
               "GPS on and off separately from the battery, which is useful so "
               "you're not powering sensitive electronics during engine start.",
        "details": [
            "Pitot heat, next to the light switches, stops ice blocking the tube that feeds your airspeed "
            "reading.",
            "The row of circuit breakers is the aircraft's fuse box. Pushed in "
            "is normal, popped out means something has tripped.",
        ],
    },
    {
        "id": "lighting",
        "title": "Exterior lighting",
        "say": "The switches along here control the outside lights. The beacon "
               "is that red rotating light warning everyone the propeller is "
               "about to turn, and it goes on before you start.",
        "details": [
            "Navigation lights are red on the left wing, green on the right, and "
            "white at the tail. They're on any time you're flying at night.",
            "Strobes are the bright flashing lights at the wingtips, for being "
            "seen in the air.",
            "Landing and taxi lights light your way on the ground and on the "
            "approach in.",
        ],
    },
    {
        "id": "flaps_trim",
        "title": "Flaps and trim",
        "say": "Flaps are that lever, with four positions: up, ten, twenty, and "
               "thirty degrees. They let you fly slower and land shorter.",
        "details": [
            "Below the panel, the trim wheel takes the pressure off the yoke so "
            "you're not fighting it to hold your pitch.",
        ],
    },
    {
        "id": "final_check",
        "title": "Parking brake and final look",
        "say": "Last thing, the parking brake, usually a small lever under the "
               "panel between your knees. Set before shutdown, released before "
               "you move.",
        "details": [
            "Doors closed and secured, seatbelts on.",
            "Take one more look across the whole panel. If anything doesn't make "
            "sense yet, that's completely normal. We'll use all of this "
            "together as we fly.",
        ],
    },
]


def as_state() -> dict:
    """Shape consumed by the reading-lesson checklist view in web/static/index.html."""
    return {"title": TITLE, "checkpoints": CHECKPOINTS}


def find(checkpoint_id: str) -> dict | None:
    return next((c for c in CHECKPOINTS if c["id"] == checkpoint_id), None)


def speech_text(checkpoint: dict, step: int | None = None) -> str:
    """Full narration for one checkpoint: "Step N: title", then the intro
    line, then every detail (details carry the actual actionable specifics:
    switch positions, what each instrument shows, not just supporting text). The
    step number + title are read first so Play All tells you which checkpoint
    you're on without needing to look at the screen."""
    title = checkpoint.get("title", "").rstrip()
    title_line = f"Step {step}: {title}" if step else title
    parts = [title_line if title_line.endswith((".", "!", "?")) else title_line + "."] if title_line else []
    parts.append(checkpoint["say"])
    parts += [d if d.rstrip().endswith((".", "!", "?")) else d.rstrip() + "." for d in checkpoint.get("details", [])]
    return " ".join(parts)
