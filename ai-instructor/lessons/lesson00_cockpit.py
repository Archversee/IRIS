"""Cockpit Familiarisation: read-along checklist content.

Companion to lesson00_preflight.py, following phrases.py's spoken-content
convention: plain Python rather than YAML, since this is narration text and not
tunable config. See lessons/lesson00_cockpit_script_DRAFT.md for provenance.

Content is written for the Cessna 172SP with the Garmin G1000 glass panel, per
profiles/aircraft/c172sp_g1000.yaml. The panel-agnostic checkpoints (seating,
engine controls, fuel, electrical, lighting, flaps, brakes) follow standard
C172 cockpit references. The PFD and MFD checkpoints describe the G1000 layout
and should be verified against the aircraft's own avionics documentation before
being treated as authoritative.

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
        "say": "In the middle of the panel, two levers control the engine. The "
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
               "extra notch. Next to it, the master switches: battery gives the "
               "aircraft electrical power, alternator keeps the battery charged "
               "once the engine's running.",
        "details": [
            "The ignition switch sits on Both once the engine is running.",
            "Battery and alternator switches are normally moved together.",
        ],
    },
    {
        "id": "fuel_system",
        "title": "Fuel system",
        "say": "Down by your feet, or on the lower console, the fuel selector "
               "picks which tank feeds the engine: Left, Both, or Right. "
               "There's also an electric fuel pump switch on the panel, and a "
               "shut-off valve for emergencies.",
        "details": [
            "Fuel selector on Both for take-off and landing.",
            "The shut-off valve is pulled out to stop fuel reaching the engine.",
        ],
    },
    {
        "id": "pfd",
        "title": "The Primary Flight Display",
        "say": "This big screen in front of you is the Primary Flight Display, and "
               "everything you need to fly is on it. Airspeed on a tape down the "
               "left side, the artificial horizon in the middle showing your "
               "pitch and bank, altitude on a tape down the right, your climb or "
               "descent rate next to that, and your heading on a compass rose "
               "along the bottom.",
        "details": [
            "The airspeed and altitude tapes replace the round dials you'd find "
            "on an older aircraft.",
            "The compass rose along the bottom carries a heading bug you can set.",
        ],
    },
    {
        "id": "mfd",
        "title": "The Multi-Function Display",
        "say": "The second screen, usually to the right, is the Multi-Function "
               "Display. It shows a moving map of where you are, engine and "
               "fuel information down the left side, and the radio frequencies "
               "along the top.",
        "details": [
            "Engine information includes RPM, oil temperature and pressure, and "
            "fuel quantity in each tank.",
            "The radios and transponder are built into this screen, rather than "
            "being separate boxes in the middle of the panel.",
        ],
    },
    {
        "id": "avionics",
        "title": "Avionics and electrical",
        "say": "Behind the yoke, the avionics master switch turns the radios and "
               "displays on and off separately from the battery, which is useful so "
               "you're not powering sensitive electronics during engine start.",
        "details": [
            "Pitot heat stops ice blocking the tube that feeds your airspeed "
            "reading.",
            "The row of circuit breakers is the aircraft's fuse box. Pushed in "
            "is normal, popped out means something has tripped.",
        ],
    },
    {
        "id": "lighting",
        "title": "Exterior lighting",
        "say": "Five switches along here control the outside lights. The beacon "
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
    switch positions, what each display shows, not just supporting text). The
    step number + title are read first so Play All tells you which checkpoint
    you're on without needing to look at the screen."""
    title = checkpoint.get("title", "").rstrip()
    title_line = f"Step {step}: {title}" if step else title
    parts = [title_line if title_line.endswith((".", "!", "?")) else title_line + "."] if title_line else []
    parts.append(checkpoint["say"])
    parts += [d if d.rstrip().endswith((".", "!", "?")) else d.rstrip() + "." for d in checkpoint.get("details", [])]
    return " ".join(parts)
