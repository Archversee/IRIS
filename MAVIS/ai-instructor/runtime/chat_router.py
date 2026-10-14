"""Chat intent routing: the deterministic word tables and clock parser the
rule-based intents in chat() run on (no LLM involved)."""
from __future__ import annotations


# weather words -> config.yaml sim.weather preset; first match wins, so
# "thunderstorm" is a storm before it's rain
WEATHER_WORDS = [(r"thunder|storm|lightning", "storm"), (r"rain|shower|drizzle|wet", "rain"),
                 (r"fog|mist|haze|murk", "fog"), (r"snow", "snow"),
                 (r"cloud|overcast|grey|gray|gloomy", "cloudy"),
                 (r"clear|sunny|blue sk|nice weather|good weather|fine weather", "clear")]
WEATHER_HINT = (r"(thunder|storm|lightning|rain|shower|drizzle|fog|mist|haze|snow|cloud|"
                r"overcast|sunny|clear sk|blue sk|weather)")
# a request, not a question: "can we get some fog" yes, "do we have rain?" no
ENV_ASK_RE = (r"\b(change|switch|make|set|turn|try|add|bring|give|want|get|put|"
              r"see what|looks? like|show me|let'?s)\b")
QUESTION_RE = r"^\s*(is|are|will|was|were|what'?s?|why|how|does|do|did|when|where|which)\b"
TIMES = {"dusk": (19, 15), "sunset": (19, 10), "sunrise": (7, 0), "dawn": (6, 45),
         "morning": (8, 0), "noon": (12, 0), "midday": (12, 0), "night": (20, 30),
         "midnight": (0, 0), "daytime": (10, 0), "evening": (19, 45),
         "afternoon": (15, 0)}
# word boundaries: "set" must not match inside "sunset"
CHANGE_RE = r"\b(change|switch|make|set|turn|try|see what|looks? like|show me)\b"
TIME_QUERY_RE = (r"(what'?s?|tell me|do you know).{0,25}\b(time|clock)\b|"
                 r"\bwhat time is it\b|\bcurrent time\b|\bthe time\s*(now|right now)?\s*\??\s*$")
TIME_HINT = (r"(time|clock|hrs|o'?clock|[ap]\.?m\b|\d{1,2}[:.]\d{2}|night|day(time)?|dusk|"
             r"dawn|sunset|sunrise|morning|noon|midnight|midday|evening|afternoon)")

# words that could make a free-text request a tool call (retry a leg, show/hide the guide, lighting)
ROUTE_HINT = (r"(again|redo|retry|rewind|checkpoint|another go|go back|guide|diagram|"
              r"controls?|show|hide|close|open|dark|bright|light|weather)")

def parse_clock(t: str):
    """Deterministic parse of explicit times: 12:45pm, 0630, 1400 hrs, 7 pm."""
    import re
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?\b", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        ap = (m.group(3) or "").replace(".", "")
        if ap == "pm" and h < 12: h += 12
        if ap == "am" and h == 12: h = 0
        if 0 <= h < 24 and 0 <= mi < 60: return h, mi
    m = re.search(r"\b(\d{3,4})\s*(hrs|hours|h)\b", t)
    if m:
        h, mi = divmod(int(m.group(1)), 100)
        if 0 <= h < 24 and 0 <= mi < 60: return h, mi
    m = re.search(r"\b(\d{1,2})\s*([ap]\.?m\.?)\b", t)
    if m:
        h = int(m.group(1)) % 12 + (12 if "p" in m.group(2) else 0)
        if 0 <= h < 24: return h, 0
    # bare 24-hour time after to/at/for: "set it to 1400", "at 0630"
    m = re.search(r"\b(?:to|at|for)\s+(\d{2})(\d{2})\b", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60: return h, mi
    return None


WEATHER_SAY = {"clear": "clear skies", "cloudy": "some cloud", "rain": "some rain",
               "storm": "a storm", "fog": "some fog", "snow": "some snow"}
