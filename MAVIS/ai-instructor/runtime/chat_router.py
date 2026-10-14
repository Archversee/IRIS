"""Chat intent routing: the trainee's free-text / voice requests.

The deterministic word tables and clock parser run first and stay
authoritative; only what they miss goes to the local LLM tool-router, and
everything else falls back to plain Q&A (build_chat)."""
from __future__ import annotations
import asyncio
import logging
import time

from agent.router import Decision, IntentRouter
from agent.tools import Tool, ToolRegistry

log = logging.getLogger("main")     # same logger name as before the move: log lines unchanged


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


PHASE_WORDS = {"takeoff": "TAKEOFF_ROLL", "runway": "PRE_TAKEOFF",
               "start": "PRE_TAKEOFF", "beginning": "PRE_TAKEOFF",
               "climb": "CLIMB_OUT", "crosswind": "CROSSWIND",
               "downwind": "DOWNWIND", "base": "BASE", "final": "FINAL",
               "landing": "FINAL", "approach": "FINAL",
               "touchdown": "FLARE_TOUCHDOWN", "flare": "FLARE_TOUCHDOWN",
               "rollout": "ROLLOUT",
               "circuit": "PRE_TAKEOFF", "lap": "PRE_TAKEOFF"}


def build_chat(rt, state, coach, loop, retry):
    """The chat action: rule-based intents first (deterministic, authoritative),
    then the local LLM may pick ONE safe tool, then plain Q&A. Returns chat().
    rt is read at call time (rt.sim / rt.engine change on a lesson switch);
    state, coach, loop and retry are fixed for the life of the app."""
    async def set_sim_time(h: int, m: int):
        await coach.say(f"Setting the clock to {h:02d}{m:02d} hours.")
        ok = await loop.run_in_executor(None, rt.sim.set_local_time, h, m)
        if not ok:
            await coach.say("The sim didn't take that time. Try again in a moment.")

    async def chat(text: str):
        if not text:
            return
        import re
        await coach.say(text, who="trainee")
        t = text.lower()
        # --- UI intents: controls guide ---
        if re.search(r"(close|hide|remove).{0,20}(guide|diagram|picture)", t):
            state["ui_cmd"] = {"action": "close_guide", "seq": time.time()}
            await coach.say("Closed. Eyes back outside, you're doing great.")
            return
        if re.search(r"(show|open|where|see|bring up).{0,30}(guide|controls|throttle|stick|brake)", t):
            state["ui_cmd"] = {"action": "open_guide", "seq": time.time()}
            await coach.say("Here's the controls guide. Just say 'close the guide' when you're done.")
            return
        if re.search(r"\bflaps?\b", t) and re.search(r"(what|why|for|explain|tell me|mean|do)", t):
            await coach.say("Flaps are wing panels that hinge down so we can fly slower, "
                            "mainly for landing.")
            return
        # --- retry intents: "let me try the landing again", "go back" ---
        if re.search(r"((try|do|fly|attempt).{0,25}again)|go back|redo|rewind|"
                     r"checkpoint|one more (try|go|time)|mess(ed)? (that |it )?up", t):
            phase = None
            for word, ph in PHASE_WORDS.items():
                if word in t:
                    phase = ph
                    break
            asyncio.create_task(retry(phase))
            return
        # --- time query: "what time is it?", read sim clock, don't guess ---
        if re.search(TIME_QUERY_RE, t) and not re.search(CHANGE_RE, t):
            lt = state.get("tel", {}).get("local_time_h")
            if lt is not None:
                h, m = int(lt) % 24, int(round((lt % 1) * 60)) % 60
                await coach.say(f"In the simulator it's {h:02d}{m:02d} hours, "
                                "that's the clock on your map. Eyes outside.")
            else:
                import datetime as _dt
                now = _dt.datetime.now()
                await coach.say(f"It's {now.strftime('%H%M')} hours here on the instructor station.")
            return
        # --- environment intents: time of day and/or weather ---
        # Requests only ("is it sunset yet?" is a question, not a change). One
        # sentence can ask for both: "make it a stormy night".
        asking = bool(re.search(ENV_ASK_RE, t)) and not re.search(QUESTION_RE, t)
        weather_ask = asking and bool(re.search(WEATHER_HINT, t))
        wcond = (next((c for pat, c in WEATHER_WORDS if re.search(pat, t)), None)
                 if weather_ask else None)

        async def _time_then_weather(h: int, m: int):
            await set_sim_time(h, m)
            if wcond:
                await _tool_weather(wcond)

        # 1) explicit clock time ("12:45pm", "1400 hrs") - deterministic, no LLM needed
        hm = parse_clock(t)
        if hm and not re.search(QUESTION_RE, t) and re.search(CHANGE_RE + r"|\b(at|to)\b", t):
            await _time_then_weather(*hm)
            return
        # 2) named time of day ("make it night", "try dusk") - deterministic;
        #    longest word first so "midnight" isn't "night", "afternoon" isn't "noon"
        if asking:
            for word in sorted(TIMES, key=len, reverse=True):
                if re.search(rf"\b{word}\b", t):
                    await _time_then_weather(*TIMES[word])
                    return
        # 3) fuzzy but time-flavoured ("just before the sun goes down") - ask the LLM
        if asking and re.search(TIME_HINT, t) and coach.llm_ok:
            hm = await coach.extract_time(text)
            if hm:
                await _time_then_weather(*hm)
                return
        # weather on its own ("make it rain", "can we get some fog")
        if weather_ask:
            if wcond:
                await _tool_weather(wcond)
            else:
                await coach.say("I can do clear, cloudy, rain, storm, fog or snow, "
                                "or a time of day. Which one?")
            return
        # --- agentic fallback: let the local LLM pick ONE safe tool for a fuzzy
        #     request the rules above didn't catch (deterministic path already ran) ---
        # A plain question ("what's ...", "why ...") is never a command, so skip the
        # tool-picking call: it is a whole extra model round trip before the answer.
        # Likewise anything with no time/weather/retry/guide flavour ("count from 10 to 1",
        # "3 - 2") can't map to a tool, so it needs no router call either.
        if re.search(QUESTION_RE, t) or not re.search(
                TIME_HINT + "|" + WEATHER_HINT + "|" + ROUTE_HINT, t):
            decision = Decision("answer", source="no-tool")
        else:
            decision = await router.decide(
                text, {"phase": rt.engine.phase, "time": state.get("tel", {}).get("local_time_h")},
                allow_regex=False)
        if (decision.kind == "tool" and decision.tool in ("set_weather", "set_time_of_day")
                and re.search(QUESTION_RE, t)):
            decision.kind = "answer"     # "is it going to rain?" asks, it doesn't order
        if decision.kind == "tool":
            tool = tools.get(decision.tool)
            log.info("agent tool -> %s %s (%s)", decision.tool, decision.args, decision.source)
            if tool:
                await tool.handler(**(decision.args or {}))
                return
        # --- everything else: free Q&A via the LLM ---
        tel = state.get("tel", {})
        snippet = {k: tel.get(k) for k in ("ias", "alt_msl", "hdg", "flaps_deg", "local_time_h")}
        await coach.answer(text, snippet, rt.engine.phase)

    # ---------- agentic tool layer (Phase 1: conversational tool-router) ----------
    # The rule-based intents in chat() still run first and stay authoritative; this
    # only catches fuzzy requests they miss, by letting the local LLM pick ONE safe,
    # validated tool. Everything degrades to plain Q&A if the model is slow/offline.
    tools = ToolRegistry()

    async def _tool_set_time(hour: int, minute: int = 0):
        await set_sim_time(int(hour) % 24, int(minute) % 60)

    async def _tool_retry(phase: str | None = None):
        ph = PHASE_WORDS.get((phase or "").lower(), phase) if phase else None
        await retry(ph)

    async def _tool_guide(action: str = "open"):
        if action == "close":
            state["ui_cmd"] = {"action": "close_guide", "seq": time.time()}
            await coach.say("Closed. Eyes back outside, you're doing great.")
        else:
            state["ui_cmd"] = {"action": "open_guide", "seq": time.time()}
            await coach.say("Here's the controls guide. Just say 'close the guide' when you're done.")

    async def _tool_weather(condition: str = ""):
        fn = getattr(rt.sim, "set_weather", None)
        ok = await loop.run_in_executor(None, fn, condition) if fn else False
        what = WEATHER_SAY.get(condition, condition)
        if ok:
            # the sim can't confirm weather back, so don't promise it arrived
            await coach.say(f"Asked the sim for {what}. Give it a moment.")
        else:
            await coach.say(f"The sim won't take {what} right now. Time of day still works.")

    tools.register(Tool(
        "set_time_of_day",
        "Change the simulator time of day / lighting (e.g. make it darker, sunset, a clock time).",
        {"hour": {"type": "integer", "description": "local hour 0-23"},
         "minute": {"type": "integer", "description": "minute 0-59", "required": False}},
        _tool_set_time, examples=["can you make it a bit darker", "set the clock to sunset"]))
    tools.register(Tool(
        "retry_phase",
        "Teleport the aeroplane back so the student can re-fly part of the circuit.",
        {"phase": {"type": "string",
                   "enum": ["takeoff", "climb", "crosswind", "downwind", "base", "final", "landing"],
                   "description": "which leg to retry; omit for the current/last one",
                   "required": False}},
        _tool_retry, examples=["let me have another go at that landing", "can we redo the downwind"]))
    tools.register(Tool(
        "controls_guide",
        "Show or hide the on-screen controls guide diagram.",
        {"action": {"type": "string", "enum": ["open", "close"],
                    "description": "open to show, close to hide", "required": False}},
        _tool_guide, examples=["where are the flaps again", "hide that diagram"]))
    tools.register(Tool(
        "set_weather",
        "Set the simulator weather to a preset (config.yaml sim.weather; MAVIS says "
        "so if the simulator won't take it).",
        {"condition": {"type": "string",
                       "enum": ["clear", "cloudy", "rain", "storm", "fog", "snow"],
                       "description": "closest weather preset to what the student asked for"}},
        _tool_weather, examples=["can you add some clouds", "make it rainy"]))

    router = IntentRouter(tools, coach._ollama, lambda: coach.llm_ok)

    return chat
