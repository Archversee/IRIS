"""External Walk-Around: read-only checklist content.

Companion to phrases.py's spoken-content convention: plain Python here, not
YAML, since this is narration text rather than tunable config. See
lessons/lesson00_preflight_script_DRAFT.md for provenance/notes. Verify
against the actual aircraft POH before treating this as authoritative.
"""

TITLE = "External Walk-Around"

CHECKPOINTS = [
    {
        "id": "cabin",
        "title": "Before you step outside",
        "say": "Before we head out, let's run the cabin check. Documents on board? "
               "Airworthiness certificate, registration, and the POH should all be here.",
        "details": [
            "Remove the control lock if fitted.",
            "Master switch ON briefly: check exterior lights (nav, beacon, strobe, "
            "landing light) and pitot heat, confirm the PFD powers up and fuel "
            "gauges register, then OFF again before you go outside.",
            "Fuel selector to BOTH.",
            "Flaps down (gives access to check them on the walk-around).",
        ],
    },
    {
        "id": "left_wing_root",
        "title": "Left wing root and tie-down",
        "say": "Starting at the left wing. First, remove the tie-down if it's still on.",
        "details": [],
    },
    {
        "id": "left_wing",
        "title": "Left wing",
        "say": "Run your hand along the leading edge, feeling for dents or damage. "
               "Check the aileron moves freely and is securely hinged. Then the flap: "
               "same thing, plus the rails and actuator.",
        "details": [
            "Stall warning vane/opening near the wingtip should move freely, not be blocked.",
            "Fuel sump: drain a sample from the left tank, check for water (sits "
            "as a separate bead) or sediment.",
            "Fuel cap secure.",
        ],
    },
    {
        "id": "left_gear",
        "title": "Left main gear",
        "say": "Down to the left wheel. Check tire inflation and wear, brake pad and "
               "disc, and the strut. It should show the right amount of compression, "
               "not collapsed or overinflated.",
        "details": [],
    },
    {
        "id": "nose",
        "title": "Nose",
        "say": "Now the nose. Cowling secure, all the screws in place. Propeller and "
               "spinner. Look for nicks or cracks. Don't just glance, run your eyes "
               "right along both blades.",
        "details": [
            "Oil: dipstick reads at least 6 quarts (POH minimum for dispatch; "
            "verify against this aircraft's actual POH figure).",
            "Air filter and intakes clear of obstruction.",
            "Nose tire condition and strut/suspension inflation.",
        ],
    },
    {
        "id": "right_gear",
        "title": "Right main gear",
        "say": "Same checks on the right wheel: tire, brake, strut.",
        "details": [],
    },
    {
        "id": "right_wing",
        "title": "Right wing",
        "say": "Right wing, working back out to the tip. Fuel sump first: drain and "
               "check for water. Then leading edge, aileron, flap, and remove the tie-down.",
        "details": [],
    },
    {
        "id": "right_fuselage",
        "title": "Right fuselage / cabin",
        "say": "Cabin door and baggage door, both closed and secured. Check the "
               "fuselage skin along here for anything unusual.",
        "details": [],
    },
    {
        "id": "empennage",
        "title": "Empennage (tail)",
        "say": "Down to the tail. Horizontal stabilizer and elevator: check the "
               "surface, then move the elevator through its full range. It should be "
               "free and not sticky. Same for the vertical stabilizer and rudder. "
               "Check the static wicks are all present. They're the thin rods on the "
               "trailing edges.",
        "details": ["Tail tie-down removed."],
    },
    {
        "id": "antennas",
        "title": "Antennas",
        "say": "Quick look along the belly and tail for the antennas. Make sure none "
               "are bent or hanging loose.",
        "details": [],
    },
    {
        "id": "final",
        "title": "Final check",
        "say": "Last thing: look at the ground under the aircraft for any fluid, and "
               "give the whole airplane one more look before we call it good. If "
               "anything felt off back there, or you're not sure about something, "
               "that's exactly the right instinct. Always ask rather than guess.",
        "details": [],
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
    oil quantity, fuel sump steps, etc., not just supporting text). The step
    number + title are read first so Play All tells you which checkpoint
    you're on without needing to look at the screen."""
    title = checkpoint.get("title", "").rstrip()
    title_line = f"Step {step}: {title}" if step else title
    parts = [title_line if title_line.endswith((".", "!", "?")) else title_line + "."] if title_line else []
    parts.append(checkpoint["say"])
    parts += [d if d.rstrip().endswith((".", "!", "?")) else d.rstrip() + "." for d in checkpoint.get("details", [])]
    return " ".join(parts)
