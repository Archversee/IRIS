"""The Coach: turns CoachEvents into gentle speech (LLM-phrased with template
fallback) and answers free-form typed questions. Fully local via Ollama."""
from __future__ import annotations
import asyncio
import logging
import time

import httpx

from core.events import CoachEvent
from . import phrases

log = logging.getLogger(__name__)

# Spoken verbatim, never sent to the LLM (timing-critical or rehearsed lines)
VERBATIM = frozenset({
    "BRIEFING", "BRIEFING_1", "BRIEFING_2", "NIGHT_ANNOUNCE", "NIGHT_AFTER", "LESSON_NOTE",
    "ROTATE_NOW", "ROTATE_LATE",
    "TURN_CROSSWIND", "TURN_DOWNWIND", "TURN_BASE", "TURN_FINAL",
    "ABEAM_FLAPS",
    "PHASE_ENTER_DEBRIEF",
    "FAM_TURN_REMIND", "FAM_FIELD_HINT", "FAM_FIELD_AHEAD", "FAM_DEBRIEF",
})


class Coach:
    def __init__(self, cfg: dict, aircraft: dict, speak_fn, transcript):
        self.url = cfg["ollama_url"].rstrip("/")
        self.model = cfg["model"]
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

    @property
    def busy(self) -> bool:
        """True while the coach loop is handling an event (incl. min-gap sleep)."""
        return self._working

    # ---------- event intake ----------
    def submit(self, ev: CoachEvent):
        if self._paused:
            return
        self._seq += 1
        # negative priority: safety first; seq keeps FIFO within severity
        self.queue.put_nowait((-ev.priority, self._seq, ev))

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
        if ev.code in ("SPEED_FAST", "SPEED_SLOW") and ev.data.get("tgt"):
            return abs(tel.get("ias", 0) - ev.data["tgt"]) <= 6
        if ev.code in ("ALT_HIGH", "ALT_LOW") and ev.data.get("tgt"):
            return abs(tel.get("alt_msl", 0) - ev.data["tgt"]) <= 60
        return False

    def _freshen(self, ev: CoachEvent) -> None:
        """Rewrite the number in a value-bearing callout to what the telemetry
        says right now, so a queue delay never makes MAVIS quote a stale figure
        (e.g. 'quick at 84' when the trainee is now at 78)."""
        tel = self.live_tel() if self.live_tel else None
        if not tel:
            return
        if ev.code in ("SPEED_FAST", "SPEED_SLOW") and tel.get("ias") is not None:
            ev.data["ias"] = round(tel["ias"])
        elif ev.code in ("ALT_HIGH", "ALT_LOW") and ev.data.get("tgt"):
            ev.data["off"] = round(abs(tel.get("alt_msl", 0) - ev.data["tgt"]))

    def resume(self):
        self._paused = False

    async def run(self):
        # corrections/safety jump ahead with a much shorter breathing gap
        gap_for = {"info": self.min_gap, "nudge": self.min_gap,
                   "correction": 2.0, "safety": 0.0}
        max_age = {"info": 3.0, "nudge": 3.0, "correction": 3.0, "safety": 30.0}
        while True:
            _, _, ev = await self.queue.get()
            if self._paused:
                continue
            self._working = True
            try:
                # drop events whose telemetry is too old, never voice 20-second-old data
                age = time.time() - ev.t
                # turn cues and lesson teaching points follow a phase call that
                # takes several seconds to speak, so they get a longer shelf life
                limit = (25.0 if ev.code.startswith("TURN_") or ev.code == "LESSON_NOTE"
                         else max_age.get(ev.severity, 3.0))
                if age > limit:
                    continue
                if self._stale(ev):
                    continue
                self._freshen(ev)   # refresh any spoken numbers to live values
                gap = time.time() - self._last_spoken
                need = gap_for.get(ev.severity, self.min_gap)
                if ev.code.startswith("TURN_"):
                    since_turn = time.time() - self._last_turn_spoken
                    if self._last_turn_spoken and since_turn < 12.0:
                        need = max(need, 12.0 - since_turn)
                if gap < need:
                    await asyncio.sleep(need - gap)
                text = await self.phrase(ev)
                if text:
                    await self.say(text, who="instructor", phase=ev.data.get("phase"))
                self._last_spoken = time.time()
                if ev.code.startswith("TURN_"):
                    self._last_turn_spoken = self._last_spoken
            finally:
                self._working = False

    async def say(self, text: str, who="instructor", phase=None, speak=True):
        async with self._speech_lock:      # serialize: finish speaking before the next line
            self.transcript.append({"who": who, "text": text, "t": time.time(), "phase": phase})
            if who == "instructor" and speak:   # speak=False: shown in chat only
                await self.speak_fn(text)       # (e.g. lines voiced by the intro video)

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
        sys = (phrases.PERSONA +
               f"\nMAVIS stands for {phrases.MAVIS_ACRONYM}, answer plainly if asked.\n"
               "The student just typed a question mid-lesson. Answer it helpfully in "
               "1-3 short spoken sentences, then hand attention back to flying.\n"
               f"Aircraft facts:\n{self.aircraft.get('facts','')}\n"
               f"Current phase: {phase}. Telemetry: {tel_snippet}\n"
               "If asked the current time, use local_time_h from telemetry (decimal hours, "
               "e.g. 10.0 = 1000 HRS), never invent a clock time.")
        # (the trainee's question is already logged to the transcript by the caller)
        out = None
        try:
            out = await self._ollama(sys, question, timeout=12.0)
            out = (out or "").strip()
        except Exception as e:
            log.warning("Q&A LLM failed: %s", e)
        if not out:
            out = self._offline_answer(question)
        if not out:
            out = ("I'll cover that on the ground. For now, "
                   "eyes up and keep flying, you're doing great.")
        import re
        out = re.sub(r",?\s*please\.?$", "", out, flags=re.I).strip()
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

    async def _ollama(self, system: str, user: str, timeout: float) -> str:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as cli:
            r = await cli.post(f"{self.url}/api/chat", json={
                "model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "keep_alive": -1,   # pin the model in VRAM, no mid-lesson reloads
                "options": {"temperature": 0.7, "num_predict": 80},
            })
            r.raise_for_status()
            self.llm_ok = True
            return r.json()["message"]["content"]

    async def check_llm(self):
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as cli:
                r = await cli.get(f"{self.url}/api/tags")
                self.llm_ok = r.status_code == 200
        except Exception:
            self.llm_ok = False
        log.info("Ollama %savailable at %s", "" if self.llm_ok else "NOT ", self.url)
