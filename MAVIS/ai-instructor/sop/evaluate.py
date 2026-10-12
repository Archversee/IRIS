"""Check a flight against its SOP / handbook rules.

ONE engine for both uses:

  live         main.py feeds every tick:   ev.step(sample) -> new findings
  post-flight  sop/results.py replays the samples IRIS recorded (flight_data)
               through the same engine, so the live callouts and the report
               can never disagree.

The engine is pure: rules + samples in, results out. No database, no sim, no
LLM. A sample is a dict in MAVIS Telemetry names and units (sop/schema.py
METRICS) plus the time and the lesson phase:

    {"t": 1712345678.2, "phase": "DOWNWIND", "ias": 78.0, "alt_msl": 1104.0,
     "alt_agl": 1039.0, "vs": -40.0, "hdg": 203.0, "bank": -2.0, "pitch": 1.5,
     "flaps_deg": 0.0, "rpm": 2350.0, "throttle": 0.62, "on_ground": False}

A value that wasn't recorded is None (or missing); rules on it then end as
"insufficient_data" instead of passing or failing on a guess.

How each kind is judged (all thresholds come from the strictness preset):

  band     target - tol_lo .. target + tol_hi (tolerances x tol_mult). An
           excursion shorter than debounce_s is noise and ignored. FAIL if any
           excursion lasts longer than sustain_s, or if more than max_out_frac
           of the rule's time was spent outside. Not judged in the first
           settle_s after a phase change (the pilot is still setting up).
  limit    min_value .. max_value, hard (no tolerance multiplier). FAIL if it
           is exceeded for debounce_s or longer.
  event    `metric op value` must be true when gate_phase begins (within
           debounce_s of the change). Without a gate it must become true at
           some point while the rule applies.
  capture  The value at liftoff / touchdown / phase entry / phase exit must
           be within min_value .. max_value.
  gaze     Not judged yet (needs AOI roles on IRIS's AOI zones) -> not_evaluated.
  unobservable  Radio calls, checklists: -> instructor (the instructor decides).

Verdicts: pass | fail | not_applicable (the rule never applied: phase never
flown, condition never true) | insufficient_data | not_evaluated | instructor.
"""
from __future__ import annotations

import math
import operator
from dataclasses import dataclass, field

from .schema import METRICS

# --------------------------------------------------------------- strictness
# How hard the check is. Chosen per session, else per trainee, else by mode.
STRICTNESS: dict[str, dict] = {
    "lenient":  {"label": "Lenient",  "debounce_s": 3.0, "sustain_s": 10.0, "settle_s": 10.0,
                 "tol_mult": 1.5, "max_out_frac": 0.25},
    "standard": {"label": "Standard (ACS-like)", "debounce_s": 2.0, "sustain_s": 5.0, "settle_s": 6.0,
                 "tol_mult": 1.0, "max_out_frac": 0.15},
    "strict":   {"label": "Strict",   "debounce_s": 1.0, "sustain_s": 2.0, "settle_s": 3.0,
                 "tol_mult": 1.0, "max_out_frac": 0.05},
}
DEFAULT_STRICTNESS = "standard"
# MAVIS mode (profiles/modes.yaml) -> strictness when nobody chose one
MODE_STRICTNESS = {"visitor": "lenient", "trainee": "standard", "pilot": "strict"}
STRICTNESS_HELP = {
    "debounce_s": "Ignore anything shorter than (s)",
    "sustain_s": "A band excursion fails after (s)",
    "settle_s": "Grace after a phase change (s)",
    "tol_mult": "Band tolerance multiplier",
    "max_out_frac": "Time allowed outside a band",
}


def pick_strictness(session: str | None = None, trainee: str | None = None,
                    mode: str | None = None) -> tuple[str, str]:
    """-> (preset, why). The session's choice wins, then the trainee's, then
    the MAVIS mode's default."""
    if session in STRICTNESS:
        return session, "set for this session"
    if trainee in STRICTNESS:
        return trainee, "set for this trainee"
    if mode in MODE_STRICTNESS:
        return MODE_STRICTNESS[mode], f"default for {mode} mode"
    return DEFAULT_STRICTNESS, "default"


def strictness_params(name: str | None) -> dict:
    return dict(STRICTNESS.get(name or "", STRICTNESS[DEFAULT_STRICTNESS]))


# --------------------------------------------------------------- samples
NOT_FLYING_PHASES = {"BRIEFING", "DEBRIEF"}
MAX_DT_S = 1.0          # a gap in the data counts as at most this much time
GROUND_AGL_FT = 12.0    # same as adapters/sim_replay.py


def sample_from_telemetry(tel: dict, phase: str | None) -> dict:
    """MAVIS Telemetry.as_dict() -> sample (live)."""
    s = {k: tel.get(k) for k in METRICS}
    s["t"] = float(tel.get("t") or 0.0)
    s["phase"] = phase
    return s


def sample_from_flight_row(row: dict, elevation_ft: float | None = None,
                           flaps_full_deg: float = 30.0) -> dict:
    """One IRIS flight_data row -> sample (post-flight). Units per the IRIS
    bridge (backend/app/mavis_bridge.py) and the CSV ingest: throttle in %,
    flaps as handle % when the angle wasn't recorded. alt_agl needs the field
    elevation (from the lesson); without it, AGL rules are insufficient_data."""
    def f(k):
        v = row.get(k)
        return None if v is None else float(v)

    alt = f("altitude_ft")
    agl = alt - elevation_ft if (alt is not None and elevation_ft is not None) else None
    flaps = f("flaps_deg")
    if flaps is None and f("flaps_handle_pct") is not None:
        flaps = f("flaps_handle_pct") / 100.0 * flaps_full_deg
    thr = f("throttle_pct")
    on_ground = row.get("on_ground")
    if on_ground is None and agl is not None:
        on_ground = agl < GROUND_AGL_FT
    ts = row.get("ts")
    t = ts.timestamp() if hasattr(ts, "timestamp") else float(ts or 0.0)
    return {
        "t": t, "phase": row.get("phase"),
        "ias": f("airspeed_kt"), "alt_msl": alt, "alt_agl": agl,
        "vs": f("vertical_speed_fpm"), "hdg": f("heading_true_deg"),
        "bank": f("bank_deg"), "pitch": f("pitch_deg"), "flaps_deg": flaps,
        "rpm": f("rpm"), "throttle": thr / 100.0 if thr is not None else None,
        "on_ground": None if on_ground is None else bool(on_ground),
    }


# --------------------------------------------------------------- helpers
_OPS = {">=": operator.ge, "<=": operator.le, "==": lambda a, b: abs(a - b) < 1e-6,
        ">": operator.gt, "<": operator.lt}


def _val(sample: dict, metric: str | None):
    if not metric:
        return None
    v = sample.get(metric)
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _diff(metric: str, v: float, ref: float) -> float:
    """v - ref; headings wrap (359 vs 1 is 2 degrees apart, not 358)."""
    if metric == "hdg":
        return (v - ref + 180.0) % 360.0 - 180.0
    return v - ref


def _unit(metric: str | None) -> str:
    u = METRICS.get(metric or "", ("", "", (0, 0), ""))[1]
    return {"frac": "", "bool": ""}.get(u, u)


def _fmt(metric: str | None, v: float | None) -> str:
    if v is None:
        return "-"
    if metric == "throttle":
        return f"{v * 100:.0f}%"
    if metric == "on_ground":
        return "on the ground" if v >= 0.5 else "airborne"
    u = _unit(metric)
    return f"{v:.0f} {u}".strip() if abs(v) >= 10 else f"{v:.1f} {u}".strip()


def rule_text(r: dict, mult: float = 1.0) -> str:
    """The requirement in a few words, e.g. '74 kt +10/-5' or 'at most 85 kt'."""
    m, k = r.get("metric"), r.get("kind")
    if k == "band":
        hi, lo = r.get("tol_hi"), r.get("tol_lo")
        u = _unit(m)
        hi_s = f"+{hi * mult:g}" if hi is not None else ""
        lo_s = f"-{lo * mult:g}" if lo is not None else ""
        tol = f"±{hi * mult:g}" if hi is not None and hi == lo else "/".join(x for x in (hi_s, lo_s) if x)
        return f"{_fmt(m, r.get('target'))} {tol}{(' ' + u) if u and hi == lo else ''}".strip()
    if k in ("limit", "capture"):
        lo, hi = r.get("min_value"), r.get("max_value")
        if lo is not None and hi is not None:
            s = f"{_fmt(m, lo)} to {_fmt(m, hi)}"
        elif hi is not None:
            s = f"at most {_fmt(m, hi)}"
        else:
            s = f"at least {_fmt(m, lo)}"
        return s + (f" at {str(r.get('capture_at')).replace('_', ' ')}" if k == "capture" else "")
    if k == "event":
        s = f"{METRICS.get(m, ('', '', 0, m))[3]} {r.get('op')} {_fmt(m, r.get('value'))}"
        return s + (f" by {r['gate_phase']}" if r.get("gate_phase") else "")
    return ""


# --------------------------------------------------------------- per-rule state
@dataclass
class _Excursion:
    t0: float
    t1: float
    phase: str | None
    peak: float            # worst value seen
    worst: float           # how far past the limit (>= 0)
    confirmed: bool = False


@dataclass
class _RuleState:
    rule: dict
    in_scope_s: float = 0.0
    out_s: float = 0.0
    seen_scope: bool = False
    seen_value: bool = False
    phase_entered_t: float | None = None
    cur: _Excursion | None = None
    excursions: list = field(default_factory=list)
    checks: list = field(default_factory=list)     # event / capture: [{t, phase, value, ok}]
    event_met: bool = False
    pending_gate: dict | None = None               # event with gate: waiting debounce after entry
    last_v: float | None = None


class RuleEvaluator:
    """Feed samples in time order with step(); read results with results()."""

    def __init__(self, rules: list[dict], strictness: str | None = None,
                 overrides: dict | None = None):
        self.strictness = strictness if strictness in STRICTNESS else DEFAULT_STRICTNESS
        self.p = {**strictness_params(self.strictness), **(overrides or {})}
        self.states = [_RuleState(r) for r in rules]
        self._prev: dict | None = None
        self._phase_t: float | None = None
        self.n_samples = 0
        self.t_first: float | None = None
        self.t_last: float | None = None

    # ---------------------------------------------------------- applicability
    def _applies(self, r: dict, s: dict) -> bool:
        ph = s.get("phase")
        phases = r.get("phases") or []
        if phases:
            if ph not in phases:
                return False
        elif ph in NOT_FLYING_PHASES:
            return False
        for c in r.get("conditions") or []:
            v = _val(s, c.get("metric"))
            fn = _OPS.get(c.get("op"))
            if v is None or fn is None or c.get("value") is None or not fn(v, float(c["value"])):
                return False
        return True

    # ---------------------------------------------------------- main step
    def step(self, s: dict) -> list[dict]:
        """Take one sample. Returns findings that became confirmed on THIS
        sample (for live callouts): a limit/band excursion that just passed
        debounce_s, a failed event gate, an out-of-range capture."""
        t = float(s["t"])
        prev = self._prev
        dt = min(max(t - prev["t"], 0.0), MAX_DT_S) if prev else 0.0
        phase_changed = prev is not None and prev.get("phase") != s.get("phase")
        if prev is None or phase_changed:
            self._phase_t = t
        if self.t_first is None:
            self.t_first = t
        self.t_last = t
        self.n_samples += 1
        out: list[dict] = []
        for st in self.states:
            k = st.rule.get("kind")
            if k == "band":
                f = self._band(st, s, dt, t)
            elif k == "limit":
                f = self._limit(st, s, dt, t)
            elif k == "event":
                f = self._event(st, s, prev, t, phase_changed)
            elif k == "capture":
                f = self._capture(st, s, prev, t, phase_changed)
            else:
                f = None
            if f:
                out.append(f)
        self._prev = s
        return out

    # ---------------------------------------------------------- band / limit
    def _bounds(self, r: dict) -> tuple[float, float]:
        if r["kind"] == "band":
            m = float(self.p["tol_mult"])
            tgt = float(r["target"])
            lo = tgt - float(r["tol_lo"]) * m if r.get("tol_lo") is not None else -math.inf
            hi = tgt + float(r["tol_hi"]) * m if r.get("tol_hi") is not None else math.inf
            return lo, hi
        lo = float(r["min_value"]) if r.get("min_value") is not None else -math.inf
        hi = float(r["max_value"]) if r.get("max_value") is not None else math.inf
        return lo, hi

    def _past(self, r: dict, v: float) -> float:
        """How far v is outside the rule's range (0 = inside)."""
        lo, hi = self._bounds(r)
        if r["kind"] == "band":
            d = _diff(r["metric"], v, float(r["target"]))
            tgt = float(r["target"])
            lo, hi = lo - tgt, hi - tgt
            return max(lo - d, d - hi, 0.0)
        return max(lo - v, v - hi, 0.0)

    def _track(self, st: _RuleState, s: dict, dt: float, t: float, v: float | None) -> dict | None:
        r = st.rule
        past = self._past(r, v) if v is not None else 0.0
        finding = None
        if past > 0:
            if st.cur is None:
                st.cur = _Excursion(t, t, s.get("phase"), v, past)
            else:
                st.cur.t1 = t
                if past > st.cur.worst:
                    st.cur.worst, st.cur.peak = past, v
                if st.cur.confirmed:
                    st.out_s += dt
            if not st.cur.confirmed and st.cur.t1 - st.cur.t0 >= self.p["debounce_s"]:
                st.cur.confirmed = True
                st.out_s += st.cur.t1 - st.cur.t0    # blips under debounce_s never count as time out
                finding = self._finding(st, st.cur.peak, t, s.get("phase"))
        else:
            self._close(st)
        return finding

    def _close(self, st: _RuleState):
        if st.cur is not None:
            if st.cur.confirmed:
                st.excursions.append(st.cur)
            st.cur = None

    def _band(self, st, s, dt, t):
        r = st.rule
        if not self._applies(r, s):
            self._close(st)
            return None
        st.seen_scope = True
        v = _val(s, r.get("metric"))
        if v is None:
            self._close(st)
            return None
        st.seen_value = True
        if self._phase_t is not None and t - self._phase_t < self.p["settle_s"]:
            self._close(st)            # settling into the new phase: not judged
            return None
        st.in_scope_s += dt
        return self._track(st, s, dt, t, v)

    def _limit(self, st, s, dt, t):
        r = st.rule
        if not self._applies(r, s):
            self._close(st)
            return None
        st.seen_scope = True
        v = _val(s, r.get("metric"))
        if v is None:
            self._close(st)
            return None
        st.seen_value = True
        st.in_scope_s += dt
        return self._track(st, s, dt, t, v)

    # ---------------------------------------------------------- event
    def _cond(self, r: dict, v: float | None) -> bool:
        fn = _OPS.get(r.get("op"))
        return v is not None and fn is not None and r.get("value") is not None and fn(v, float(r["value"]))

    def _event(self, st, s, prev, t, phase_changed):
        r = st.rule
        v = _val(s, r.get("metric"))
        if v is not None:
            st.seen_value = True
        gate = r.get("gate_phase")
        if not gate:
            if self._applies(r, s):
                st.seen_scope = True
                if self._cond(r, v):
                    st.event_met = True
            return None
        # gated: judged at the start of each entry into gate_phase
        if phase_changed and s.get("phase") == gate:
            st.seen_scope = True
            st.pending_gate = {"t": t, "phase": gate, "value": v, "ok": self._cond(r, v)}
        g = st.pending_gate
        if g is None:
            return None
        if not g["ok"] and self._cond(r, v):     # late by less than debounce_s: still fine
            g["ok"], g["value"] = True, v
        if g["ok"] or t - g["t"] >= self.p["debounce_s"] or s.get("phase") != gate:
            if not g["ok"]:
                g["value"] = v if v is not None else g["value"]
            st.checks.append(g)
            st.pending_gate = None
            if not g["ok"]:
                return self._finding(st, g["value"], t, gate)
        return None

    # ---------------------------------------------------------- capture
    def _capture(self, st, s, prev, t, phase_changed):
        r = st.rule
        if prev is None:
            return None
        at = r.get("capture_at")
        phases = r.get("phases") or []
        pick = None                         # (sample to read, phase)
        if at in ("liftoff", "touchdown"):
            g0, g1 = prev.get("on_ground"), s.get("on_ground")
            if g0 is None or g1 is None:
                return None
            if at == "liftoff" and g0 and not g1:
                pick = s                     # first airborne sample
            elif at == "touchdown" and not g0 and g1:
                pick = prev                  # last airborne sample (sink rate before contact)
            if pick is not None and phases and prev.get("phase") not in phases and s.get("phase") not in phases:
                pick = None
        elif phase_changed:
            if at == "phase_entry" and s.get("phase") in phases:
                pick = s
            elif at == "phase_exit" and prev.get("phase") in phases:
                pick = prev
        if pick is None:
            return None
        if not self._conds_only(r, pick):
            return None
        st.seen_scope = True
        v = _val(pick, r.get("metric"))
        if v is None:
            return None
        st.seen_value = True
        ok = self._past(r, v) == 0
        st.checks.append({"t": float(pick["t"]), "phase": pick.get("phase"), "value": v, "ok": ok})
        return None if ok else self._finding(st, v, t, pick.get("phase"))

    def _conds_only(self, r, s) -> bool:
        return self._applies({**r, "phases": [s.get("phase")]}, s)

    # ---------------------------------------------------------- output
    def _finding(self, st: _RuleState, value, t, phase) -> dict:
        r = st.rule
        f = {"rule_id": r.get("id"), "title": r.get("title"), "kind": r.get("kind"),
             "severity": r.get("severity") or "caution", "metric": r.get("metric"),
             "value": value, "value_text": _fmt(r.get("metric"), value),
             "requirement": rule_text(r, self.p["tol_mult"]), "t": t, "phase": phase}
        if r.get("kind") in ("band", "limit") and r.get("metric") != "hdg":
            lo, hi = self._bounds(r)          # lets the coach drop a callout once it's fixed
            f["lo"], f["hi"] = (None if math.isinf(lo) else lo), (None if math.isinf(hi) else hi)
        return f

    def active(self) -> list[dict]:
        """Rules being broken right now (confirmed and still going) - live HUD."""
        out = []
        for st in self.states:
            if st.cur is not None and st.cur.confirmed:
                f = self._finding(st, st.cur.peak, st.cur.t1, st.cur.phase)
                f["since"] = st.cur.t0
                out.append(f)
        return out

    def results(self) -> list[dict]:
        """One result per rule. Safe to call mid-flight (live summary); an
        excursion still in progress is included."""
        res = []
        for st in self.states:
            r = st.rule
            k = r.get("kind")
            exc = list(st.excursions) + ([st.cur] if st.cur is not None and st.cur.confirmed else [])
            viol = [{"t0": e.t0, "t1": e.t1, "dur_s": round(e.t1 - e.t0, 1), "phase": e.phase,
                     "peak": e.peak, "peak_text": _fmt(r.get("metric"), e.peak)} for e in exc]
            metrics: dict = {}
            if k in ("gaze",):
                verdict, summary = "not_evaluated", "Gaze rules are not checked yet."
            elif k == "unobservable" or k not in ("band", "limit", "event", "capture"):
                verdict, summary = "instructor", "Can't be measured from flight data: the instructor decides."
            elif st.pending_gate is not None:
                st.checks.append(st.pending_gate)
                st.pending_gate = None
                verdict, summary = self._verdict(st, k, viol, metrics)
            else:
                verdict, summary = self._verdict(st, k, viol, metrics)
            res.append({"rule_id": r.get("id"), "title": r.get("title"), "kind": k,
                        "severity": r.get("severity") or "caution", "metric": r.get("metric"),
                        "phases": r.get("phases") or [], "layer": r.get("layer"),
                        "requirement": rule_text(r, self.p["tol_mult"]),
                        "verdict": verdict, "summary": summary, "metrics": metrics,
                        "violations": viol, "checks": st.checks,
                        "source_quote": r.get("source_quote"), "source_page": r.get("source_page")})
        return res

    def _verdict(self, st: _RuleState, k: str, viol: list, metrics: dict) -> tuple[str, str]:
        r = st.rule
        if not st.seen_scope:
            where = ", ".join(r.get("phases") or []) or "the flight"
            return "not_applicable", f"Didn't apply in this flight ({where})."
        if not st.seen_value:
            return "insufficient_data", f"{METRICS.get(r.get('metric'), ('', '', 0, r.get('metric')))[3]} wasn't recorded."
        if k in ("band", "limit"):
            frac_out = st.out_s / st.in_scope_s if st.in_scope_s > 0 else 0.0
            metrics.update(time_in_scope_s=round(st.in_scope_s, 1), time_out_s=round(st.out_s, 1),
                           frac_within=round(1 - frac_out, 3) if st.in_scope_s > 0 else None,
                           n_violations=len(viol))
            if k == "band":
                if st.in_scope_s <= 0:
                    return "not_applicable", "Only flown during the settling time after a phase change."
                long = [v for v in viol if v["dur_s"] > self.p["sustain_s"]]
                metrics["n_sustained"] = len(long)
                pct = f"Within {rule_text(r, self.p['tol_mult'])} {100 * (1 - frac_out):.0f}% of the time"
                if long:
                    w = max(long, key=lambda v: v["dur_s"])
                    return "fail", f"{pct}; out for {w['dur_s']:.0f} s (peak {w['peak_text']})."
                if frac_out > self.p["max_out_frac"]:
                    return "fail", f"{pct} (needs {100 * (1 - self.p['max_out_frac']):.0f}%)."
                return "pass", pct + (f"; {len(viol)} short excursion(s) corrected." if viol else ".")
            if viol:
                w = max(viol, key=lambda v: abs(v["peak"] or 0))
                return "fail", (f"Exceeded {len(viol)} time(s), {sum(v['dur_s'] for v in viol):.0f} s in all "
                                f"(worst {w['peak_text']}).")
            return "pass", f"Stayed {rule_text(r)}."
        if k == "event":
            if not r.get("gate_phase"):
                return ("pass", "Done.") if st.event_met else ("fail", f"Never done: {rule_text(r)}.")
            bad = [c for c in st.checks if not c["ok"]]
            metrics.update(n_checks=len(st.checks), n_failed=len(bad))
            if bad:
                return "fail", (f"Not done by {r['gate_phase']} ({_fmt(r.get('metric'), bad[0]['value'])} "
                                f"when it began).")
            return "pass", f"Done by {r['gate_phase']}."
        # capture
        bad = [c for c in st.checks if not c["ok"]]
        metrics.update(n_checks=len(st.checks), n_failed=len(bad),
                       values=[c["value"] for c in st.checks])
        if not st.checks:
            return "not_applicable", f"No {str(r.get('capture_at')).replace('_', ' ')} in this flight."
        if bad:
            return "fail", (f"{_fmt(r.get('metric'), bad[0]['value'])} at "
                            f"{str(r.get('capture_at')).replace('_', ' ')} (needs {rule_text(r).split(' at ')[0]}).")
        return "pass", f"{_fmt(r.get('metric'), st.checks[-1]['value'])} at {str(r.get('capture_at')).replace('_', ' ')}."


def summarize(results: list[dict]) -> dict:
    """Counts per verdict, and how many failures per severity."""
    out = {"total": len(results), "by_verdict": {}, "failed_by_severity": {}}
    for x in results:
        v = x.get("final_verdict") or x["verdict"]
        out["by_verdict"][v] = out["by_verdict"].get(v, 0) + 1
        if v == "fail":
            sv = x.get("severity") or "caution"
            out["failed_by_severity"][sv] = out["failed_by_severity"].get(sv, 0) + 1
    return out


def evaluate(rules: list[dict], samples, strictness: str | None = None,
             overrides: dict | None = None) -> list[dict]:
    """Post-flight convenience: run all samples, return results."""
    ev = RuleEvaluator(rules, strictness, overrides)
    for s in samples:
        ev.step(s)
    return ev.results()
