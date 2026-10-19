"""The Coach: turns CoachEvents into gentle speech (LLM-phrased with template
fallback) and answers free-form typed questions. Fully local via Ollama."""
from __future__ import annotations
import asyncio
import logging
import re
import time

import httpx

from core.events import CoachEvent
from . import phrases

log = logging.getLogger(__name__)

# Spoken verbatim, never sent to the LLM (timing-critical or rehearsed lines)
VERBATIM = frozenset({
    "BRIEFING", "BRIEFING_1", "BRIEFING_2", "NIGHT_ANNOUNCE", "NIGHT_AFTER", "LESSON_NOTE",
    "ROTATE_NOW", "ROTATE_LATE",
    "TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL", "ROLL_OUT_LEG",
    "PRE_TURN", "PRE_TURN_ALT",
    "ABEAM_FLAPS",
    "PHASE_ENTER_DEBRIEF",
    "FAM_TURN_REMIND", "FAM_YAW_REMIND", "FAM_STALL",
    "FAM_ROLL_OUT", "FAM_CENTRE_PEDALS", "FAM_THROTTLE_BACK", "FAM_LEVEL_OFF",
    "SINK_HIGH_FAST",   # the fix is the nose, not power: never reworded into "add power"
    "ALT_LOW_FAST", "ALT_HIGH_SLOW", "FAM_FAST_DIVE",   # same: nose, not power
    "SPEED_FAST_LEVEL", "SPEED_SLOW_LEVEL",             # level flight: power, not nose
    "FAM_FIELD_HINT", "FAM_DEBRIEF", "FAM_DEBRIEF_DONE",
    "FAM_DEMO_START", "FAM_DEMO_END", "FAM_STEP_CUE", "FAM_DEMO_SKIPPED",
    "SOP_RULE",           # quotes the SOP's own numbers: never let the LLM rephrase them
    "OFF_TRACK",          # left/right must never be flipped by a rewording
})



# Speaking order, highest first: safety > timing-critical calls (worthless if
# late: turn / roll-out / rotate / level-off) > corrections > nudges > info.
# A timing-critical or safety line cuts off a less important one mid-sentence.
RANK_URGENT, RANK_SAFETY = 3, 4
_URGENT = frozenset({"TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL",
                     "ROLL_OUT_LEG", "PRE_TURN", "PRE_TURN_ALT", "ROTATE_NOW", "ROTATE_LATE", "FAM_ROLL_OUT",
                     "FAM_CENTRE_PEDALS", "FAM_THROTTLE_BACK", "FAM_LEVEL_OFF"})
SAY_RANK = 2   # lines said directly (answers, chat replies): cut off only by urgent/safety


def rank(ev: CoachEvent) -> int:
    if ev.severity == "safety":
        return RANK_SAFETY
    if ev.code in _URGENT:
        return RANK_URGENT
    return ev.priority     # info 0, nudge 1, correction 2


# "now come back" calls (core.familiarization.PART_CUES): may wait a little
# behind a line already being spoken
_PART_CUES = frozenset({"FAM_ROLL_OUT", "ROLL_OUT_LEG", "FAM_CENTRE_PEDALS", "FAM_THROTTLE_BACK", "FAM_LEVEL_OFF"})


def clip_spoken(text: str, max_words: int = 30) -> str:
    """Keep a spoken line short: whole sentences up to max_words (at least the
    first sentence, cut at a word if even that runs long). Models don't always
    honour a length limit in the prompt."""
    text = " ".join((text or "").split())
    if len(text.split()) <= max_words:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, n = [], 0
    for snt in sentences:
        w = len(snt.split())
        if out and n + w > max_words:
            break
        out.append(snt)
        n += w
    clipped = " ".join(out)
    if len(clipped.split()) > max_words:
        clipped = " ".join(clipped.split()[:max_words]).rstrip(",;:") + "."
    return clipped

class Coach:
    def __init__(self, cfg: dict, aircraft: dict, speak_fn, transcript):
        self.url = cfg["ollama_url"].rstrip("/")
        self.model = cfg["model"]
        self._think: bool | None = False   # see _ollama
        self.timeout = cfg.get("timeout_s", 1.5)
        self.min_gap = cfg.get("min_speak_interval_s", 8)
        self.aircraft = aircraft
        self.speak_fn = speak_fn          # async fn(text) -> plays TTS
        self.transcript = transcript      # shared list for UI
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._speech_lock = asyncio.Lock()   # one voice at a time, never overlap
        self._last_spoken = 0.0
        self.live_tel = None                 # callable -> current telemetry dict
        self._recent: list[str] = []
        self._seq = 0
        self.llm_ok = True
        self._paused = False
        self._last_turn_spoken = 0.0
        self._working = False   # True while run() is processing a dequeued event
        self._speaking_rank: int | None = None   # rank of the line being spoken now
        self.interrupt = None   # fn() that cuts off the line being spoken (rt.stop_speech)

    @property
    def busy(self) -> bool:
        """True while the coach loop is handling an event (incl. min-gap sleep)."""
        return self._working

    # ---------- event intake ----------
    def submit(self, ev: CoachEvent):
        if self._paused:
            return
        self._seq += 1
        r = rank(ev)
        # negative rank: most important first; seq keeps FIFO within a rank
        self.queue.put_nowait((-r, self._seq, ev))
        if (r >= RANK_URGENT and self.interrupt and self._speaking_rank is not None
                and r > self._speaking_rank):
            log.info("%s cuts off a less important line", ev.code)
            self.interrupt()

    def _outranked(self, r: int) -> bool:
        """True if something more important than rank r is waiting."""
        q = self.queue._queue      # heap: [0] is the most important item
        return bool(q) and -q[0][0] > r

    async def _wait(self, seconds: float, r: int) -> bool:
        """Sleep, but give up early (False) once a more important line is waiting."""
        end = time.time() + seconds
        while time.time() < end:
            if self._outranked(r):
                return False
            await asyncio.sleep(min(0.1, end - time.time()))
        return True

    def pause(self):
        """Stop queuing new callouts and drop anything already waiting."""
        self._paused = True
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _stale(self, ev: CoachEvent) -> bool:
        """True if the situation already resolved itself - saying it now would
        contradict what the trainee sees (e.g. 'too fast at 84' when the
        speed is back at 74)."""
        tel = self.live_tel() if self.live_tel else None
        if not tel:
            return False
        if ev.code.startswith(("SPEED_FAST", "SPEED_SLOW")) and ev.data.get("tgt"):
            return abs(tel.get("ias", 0) - ev.data["tgt"]) <= 6
        if ev.code.startswith(("ALT_HIGH", "ALT_LOW")) and ev.data.get("tgt"):
            return abs(tel.get("alt_msl", 0) - ev.data["tgt"]) <= 60
        if ev.code == "ROLL_OUT_LEG":
            return abs(tel.get("bank", 0)) < 5     # wings already level
        if ev.code == "SOP_RULE" and ev.data.get("metric") and tel.get(ev.data["metric"]) is not None:
            v = float(tel[ev.data["metric"]])      # back inside the rule's range already
            lo, hi = ev.data.get("lo"), ev.data.get("hi")
            return (lo is not None or hi is not None) and (lo is None or v >= lo) and (hi is None or v <= hi)
        return False

    def _freshen(self, ev: CoachEvent) -> None:
        """Rewrite the number in a value-bearing callout to what the telemetry
        says right now, so a queue delay never makes MAVIS quote a stale figure
        (e.g. 'quick at 84' when the trainee is now at 78)."""
        tel = self.live_tel() if self.live_tel else None
        if not tel:
            return
        if ev.code.startswith(("SPEED_FAST", "SPEED_SLOW")) and tel.get("ias") is not None:
            ev.data["ias"] = round(tel["ias"])
        elif ev.code.startswith(("ALT_HIGH", "ALT_LOW")) and ev.data.get("tgt"):
            ev.data["off"] = round(abs(tel.get("alt_msl", 0) - ev.data["tgt"]))

    def resume(self):
        self._paused = False

    async def run(self):
        # corrections/safety jump ahead with a much shorter breathing gap
        gap_for = {"info": self.min_gap, "nudge": self.min_gap,
                   "correction": 2.0, "safety": 0.0}
        put_back = self.queue.put_nowait
        max_age = {"info": 3.0, "nudge": 3.0, "correction": 3.0, "safety": 30.0}
        while True:
            neg_r, seq, ev = await self.queue.get()
            if self._paused:
                continue
            r = -neg_r
            self._working = True
            try:
                # drop events whose telemetry is too old, never voice 20-second-old data
                age = time.time() - ev.t
                # turn cues and lesson teaching points follow a phase call that
                # takes several seconds to speak, so they get a longer shelf life
                limit = (25.0 if ev.code.startswith("TURN_") or ev.code == "LESSON_NOTE"
                         else 12.0 if ev.code in _PART_CUES
                         else max_age.get(ev.severity, 3.0))
                if age > limit:
                    continue
                if self._stale(ev):
                    continue
                self._freshen(ev)   # refresh any spoken numbers to live values
                gap = time.time() - self._last_spoken
                need = 0.0 if r >= RANK_URGENT else gap_for.get(ev.severity, self.min_gap)
                if ev.code.startswith("TURN_"):
                    since_turn = time.time() - self._last_turn_spoken
                    if self._last_turn_spoken and since_turn < 12.0:
                        need = max(need, 12.0 - since_turn)
                if gap < need and not await self._wait(need - gap, r):
                    put_back((neg_r, seq, ev))   # something more important first
                    continue
                text = await self.phrase(ev)
                if self._outranked(r):           # arrived while the LLM was phrasing
                    put_back((neg_r, seq, ev))
                    continue
                if text:
                    await self.say(text, who="instructor", phase=ev.data.get("phase"), rank=r)
                self._last_spoken = time.time()
                if ev.code.startswith("TURN_"):
                    self._last_turn_spoken = self._last_spoken
            finally:
                self._working = False

    async def say(self, text: str, who="instructor", phase=None, speak=True, rank=SAY_RANK):
        async with self._speech_lock:      # serialize: finish speaking before the next line
            self.transcript.append({"who": who, "text": text, "t": time.time(), "phase": phase})
            if who == "instructor" and speak:   # speak=False: shown in chat only
                self._speaking_rank = rank      # (e.g. lines voiced by the intro video)
                try:
                    await self.speak_fn(text)
                finally:
                    self._speaking_rank = None

    # ---------- phrasing ----------
    async def phrase(self, ev: CoachEvent) -> str:
        fallback = phrases.render(ev.code, ev.data)
        # scripted / timing-critical lines are spoken verbatim
        if (ev.code in VERBATIM or ev.code.startswith("PHASE_ENTER_")
                or not self.llm_ok):
            return fallback
        prompt = (f"Event: {ev.code} severity={ev.severity} data={ev.data}.\n"
                  f"Recent things you already said (do not repeat): {self._recent[-4:]}.\n"
                  f"Example of acceptable tone: \"{fallback}\"\n"
                  "Say your next single coaching sentence now.")
        try:
            out = await self._ollama(phrases.PERSONA, prompt, self.timeout)
            out = (out or "").strip().strip('"')
            import re
            out = re.sub(r",?\s*please\.?$", "", out, flags=re.I).strip()
            if 0 < len(out) < 160:
                self._recent.append(out)
                return out
        except Exception as e:
            log.debug("LLM fallback (%s)", e)
        return fallback

    # ---------- typed Q&A ----------
    _QA_FALLBACKS = (
        (r"\bflaps?\b", "Flaps are panels on the wing that hinge down, they let you fly "
         "slower and more safely for landing, and we use them on downwind, base, and final. "
         "Eyes outside, you're doing fine."),
        (r"\b(rudder|pedals?)\b", "The rudder pedals steer the nose left and right on the ground "
         "and help you stay coordinated in the air. Left foot pushes the nose left."),
        (r"\b(throttle|power)\b", "The throttle lever on your left controls engine power, "
         "push forward for more, pull back for less."),
        (r"\b(aileron|stick|yoke)\b", "The stick moves the ailerons on the wings, "
         "bank left or right to turn."),
    )

    def _offline_answer(self, question: str) -> str | None:
        import re
        t = question.lower()
        if re.search(r"\bmavis\b", t) and re.search(
                r"(stand|stands for|mean|who are you|who is|your name|acronym|short for)", t):
            return (f"MAVIS stands for {phrases.MAVIS_ACRONYM}, "
                    "I'm your verbal flight instructor for this circuit.")
        for pat, ans in self._QA_FALLBACKS:
            if re.search(pat, t) and re.search(r"(what|why|how|explain|tell|mean|for|do)", t):
                return ans
        return None

    async def answer(self, question: str, tel_snippet: dict, phase: str) -> str:
        # kept short on purpose: it's read aloud mid-flight and shown in chat
        sys = (phrases.PERSONA +
               f"\nMAVIS stands for {phrases.MAVIS_ACRONYM}, answer plainly if asked.\n"
               "The student just asked a question mid-lesson. Answer in one or two short "
               "spoken sentences, 25 words at most in total. No preamble, no filler.\n"
               f"Aircraft facts:\n{self.aircraft.get('facts','')}\n"
               f"Current phase: {phase}. Telemetry: {tel_snippet}\n"
               "If asked the current time, use local_time_h from telemetry (decimal hours, "
               "e.g. 10.0 = 1000 HRS), never invent a clock time.")
        # (the trainee's question is already logged to the transcript by the caller)
        out = None
        why = ""
        try:
            out = await self._ollama(sys, question, timeout=40.0)
            out = (out or "").strip()
            if not out:
                why = f"The model {self.model} returned an empty reply."
        except Exception as e:
            why = self._llm_failure(e)
            log.warning("Q&A LLM failed: %s -- %s", type(e).__name__, why)
        if not out:
            out = self._offline_answer(question)
        if not out and why:
            # say what is actually wrong instead of pretending it is a teaching call
            out = why
        if not out:
            out = ("I'll cover that on the ground. For now, "
                   "eyes up and keep flying, you're doing great.")
        import re
        out = re.sub(r",?\s*please\.?$", "", out, flags=re.I).strip()
        out = clip_spoken(out, 30)
        await self.say(out)
        return out

    async def extract_time(self, text: str) -> tuple[int, int] | None:
        """LLM interpretation of fuzzy time-of-day requests -> (hour, minute).
        Returns None if the student isn't asking for a time change."""
        sys = ('A flight-sim student may be asking to change the simulator time of day. '
               'Respond with ONLY compact JSON, no prose, no code fences. '
               'If they want a time change: {"hour": H, "minute": M} with H 0-23, M 0-59 '
               '(interpret words: dawn~06:45, sunrise~07:00, morning~08:00, noon~12:00, '
               'afternoon~15:00, golden hour/sunset~19:10, dusk~19:20, evening~19:45, '
               'night~20:30, midnight~00:00). '
               'If they are NOT asking to change the time: {"hour": null}')
        try:
            import json
            out = await self._ollama(sys, text, timeout=6.0)
            out = out.strip().strip("`").replace("json", "", 1).strip()
            d = json.loads(out[out.find("{"):out.rfind("}") + 1])
            if d.get("hour") is None:
                return None
            h, m = int(d["hour"]) % 24, int(d.get("minute") or 0) % 60
            return h, m
        except Exception as e:
            log.debug("extract_time failed: %s", e)
            return None

    def _llm_failure(self, e: Exception) -> str:
        """A one-sentence reason a question got no answer from the model."""
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            if code == 404:
                return (f"Ollama doesn't have the model {self.model}. "
                        f"On the Ollama PC run: ollama pull {self.model}")
            return f"Ollama at {self.url} returned an error ({code})."
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
            return (f"I can't reach the language model at {self.url}. "
                    "Check Ollama is running and the address in config.yaml is right.")
        if isinstance(e, httpx.TimeoutException):
            return f"The language model at {self.url} took too long to answer."
        return f"The language model failed ({type(e).__name__})."

    async def _ollama(self, system: str, user: str, timeout: float) -> str:
        # Reasoning models (Qwen3.x, Gemma 4, DeepSeek-R1...) spend the token budget
        # on a hidden "thinking" channel and leave message.content empty, so thinking
        # is switched off (builds too old to know the flag get it left out).
        body = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "keep_alive": -1,   # pin the model in VRAM, no mid-lesson reloads
            "options": {"temperature": 0.7, "num_predict": 200},
        }
        if self._think is not None:
            body["think"] = self._think
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as cli:
            r = await cli.post(f"{self.url}/api/chat", json=body)
            if r.status_code == 400 and "think" in body and "think" in r.text.lower():
                self._think = None
                body.pop("think")
                r = await cli.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
            self.llm_ok = True
            j = r.json()
            msg = j.get("message") or {}
            # Ollama's own timings say where the time went: load_duration is the model
            # (re)loading into memory, eval_duration is generating, prompt_eval the input
            ns = 1e9
            gen_s = (j.get("eval_duration") or 0) / ns
            log.info("LLM %s: total %.1fs (load %.1fs, prompt %s tok %.1fs, generate %s tok %.1fs = %.0f tok/s, "
                     "thinking %s chars)",
                     self.model, (j.get("total_duration") or 0) / ns, (j.get("load_duration") or 0) / ns,
                     j.get("prompt_eval_count"), (j.get("prompt_eval_duration") or 0) / ns,
                     j.get("eval_count"), gen_s, (j.get("eval_count") or 0) / gen_s if gen_s else 0,
                     len(msg.get("thinking") or ""))
            out = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
            if not out:
                log.warning("Ollama returned no answer text (model %s, thinking field %s chars, "
                            "done_reason %s)", self.model, len(msg.get("thinking") or ""),
                            r.json().get("done_reason"))
            return out

    async def check_llm(self):
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as cli:
                r = await cli.get(f"{self.url}/api/tags")
                self.llm_ok = r.status_code == 200
        except Exception:
            self.llm_ok = False
        log.info("Ollama %savailable at %s", "" if self.llm_ok else "NOT ", self.url)
        if self.llm_ok:
            # loading the model takes seconds; do it now, not on the first callout
            asyncio.create_task(self._warm_up())

    async def _warm_up(self):
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as cli:
                r = await cli.post(f"{self.url}/api/generate",
                                   json={"model": self.model, "keep_alive": -1})
                r.raise_for_status()
            log.info("LLM %s loaded", self.model)
        except Exception as e:
            log.warning("LLM warm-up failed: %s", self._llm_failure(e))
