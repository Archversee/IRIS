"""One way of writing units in the AI debrief text: "knots" not "kt", "degrees" not "deg" / "°".
(A copy of MAVIS/ai-instructor/voice/units.py, so the two write units the same way; keep them in step.)

standard_units(): what the chat / transcript shows (and what TTS gets as its input).
spoken_units():   the extra expansions only the voice needs ("RPM" -> "R P M", "%" -> "percent").

Only abbreviations that follow a number are touched ("70kt", "25 deg", "1,100 ft"), so
words like "aircraft" or "left" are never rewritten. The numbers themselves never change.
"""
from __future__ import annotations

import re

_N = r"(\d(?:[\d,]*\d)?(?:\.\d+)?)"          # 70 · 1,100 · 2.5
_RANGE = rf"{_N}(\s*(?:-|–|to)\s*{_N})?"      # 70-80 · 25 to 35


def _u(pattern: str, word: str, plural: str | None = None):
    """number + unit -> number + word (singular for exactly 1)."""
    rx = re.compile(rf"{_RANGE}\s*(?:{pattern})(?![\w/])", re.I)

    def repl(m):
        single = m.group(3) is None and m.group(1) in ("1", "1.0")
        return f"{m.group(1)}{m.group(2) or ''} {word if single or plural is None else plural}"
    return rx, repl


# order matters: "ft/min" before "ft", "°C" before "°"
_DISPLAY = [
    _u(r"ft\s*/\s*min|fpm|ft\s+per\s+min(?:ute)?|feet\s*/\s*min", "foot per minute", "feet per minute"),
    _u(r"kts?|kias|ktas|knots?", "knot", "knots"),
    _u(r"°\s*c\b|deg(?:rees?)?\s*c(?:elsius)?\b", "degree Celsius", "degrees Celsius"),
    _u(r"°\s*f\b|deg(?:rees?)?\s*f(?:ahrenheit)?\b", "degree Fahrenheit", "degrees Fahrenheit"),
    _u(r"°|degs?|degrees?", "degree", "degrees"),
    _u(r"ft|feet|foot", "foot", "feet"),
    _u(r"nmi?|nautical\s+miles?", "nautical mile", "nautical miles"),
    _u(r"secs?|seconds?", "second", "seconds"),
    _u(r"mins?|minutes?", "minute", "minutes"),
    _u(r"hrs?(?=\s|$|[.,;!?])", "hour", "hours"),
]
_WORDS = [
    (re.compile(r"\bV/S\b"), "vertical speed"),
    (re.compile(r"\bhdg\b", re.I), "heading"),
]

_SPOKEN = [
    (re.compile(r"(\d)\s*[-–]\s*(\d)"), r"\1 to \2"),       # "70-80 knots" -> "70 to 80 knots"
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\brpm\b", re.I), "R P M"),
    (re.compile(r"\bAGL\b"), "above ground level"),
    (re.compile(r"\bMSL\b"), "above sea level"),
    (re.compile(r"\bQNH\b"), "Q N H"),
    (re.compile(r"\bIAS\b"), "indicated airspeed"),
    (re.compile(r"±\s*"), "plus or minus "),
]


def standard_units(text: str) -> str:
    """'Hold 70kt, bank 25 deg, 1,100 ft' -> 'Hold 70 knots, bank 25 degrees, 1,100 feet'."""
    if not text:
        return text
    out = text
    for rx, repl in _DISPLAY:
        out = rx.sub(repl, out)
    for rx, word in _WORDS:
        out = rx.sub(word, out)
    return out


def spoken_units(text: str) -> str:
    out = standard_units(text)
    for rx, word in _SPOKEN:
        out = rx.sub(word, out)
    return out


if __name__ == "__main__":       # python -m app.units
    cases = {
        "Hold 70kt, bank 25 deg, climb to 1,100 ft.": "Hold 70 knots, bank 25 degrees, climb to 1,100 feet.",
        "Speed 70-80 KTS": "Speed 70-80 knots",
        "Sink 500 fpm": "Sink 500 feet per minute",
        "Descend at 500 ft/min": "Descend at 500 feet per minute",
        "Turn 1 deg left": "Turn 1 degree left",
        "Bank 25°": "Bank 25 degrees",
        "OAT 15°C": "OAT 15 degrees Celsius",
        "The aircraft is left of the line": "The aircraft is left of the line",
        "Hold for 5 secs, then 2 mins": "Hold for 5 seconds, then 2 minutes",
        "1400 hrs": "1400 hours",
        "3 nm final": "3 nautical miles final",
        "65 KIAS on final": "65 knots on final",
        "Hold 74 knots": "Hold 74 knots",
        "2,300 rpm": "2,300 rpm",
    }
    bad = 0
    for src, want in cases.items():
        got = standard_units(src)
        if got != want:
            bad += 1
            print(f"FAIL {src!r}: {got!r} != {want!r}")
    for src, want in {"2,300 rpm, 50% power, 1000 ft AGL": "2,300 R P M, 50 percent power, 1000 feet above ground level",
                      "Speed 70-80 kt": "Speed 70 to 80 knots"}.items():
        got = spoken_units(src)
        if got != want:
            bad += 1
            print(f"FAIL spoken {src!r}: {got!r} != {want!r}")
    print("all passed" if not bad else f"{bad} failed")
