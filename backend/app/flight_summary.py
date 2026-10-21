"""The whole-flight debrief (Review → Debrief): facts about one flight, and
the prompt that turns them into a short summary for the student.

Pure: routers/debrief.py gathers the data. Like the per-incident note
(app/incident_explain.py), the model only sees measured facts and every
number in its answer is checked against them.
"""
from __future__ import annotations

from .incident_explain import KNOWLEDGE, compact_json
from .progress import CAUSES, coaching_summary

PROMPT_VERSION = "1"
SECTIONS = ("Overview", "What went well", "What to work on", "Next flight")
MAX_INCIDENTS = 15


def _mmss(s: float | None) -> str | None:
    if s is None:
        return None
    s = max(0, round(s))
    return f"{s // 60}:{s % 60:02d}"


def _r(v, d=1):
    return round(v, d) if isinstance(v, (int, float)) else v


def build_facts(session: dict, evaluation: dict | None, rules: list[dict], incidents: list[dict],
                stats: dict | None, previous: dict | None) -> dict:
    """session: {name, pilot, lesson_id, started_at, flight_s}
    evaluation: {strictness, signed_off, instructor_comment} or None (never checked)
    rules: [{title, severity, verdict, breaches}]   (verdict = instructor's if set)
    incidents: [{title, t_s (since start), phase, peak_text, dur_s, findings: [{code, kind, text}] | None}]
    stats: progress.session_stats() or None
    previous: {date, rules_broken: [titles], times_broken} for the pilot's flight before, or None"""
    met = [r["title"] for r in rules if r["verdict"] == "pass"]
    broken = [{"rule": r["title"], "severity": r["severity"], "times": r["breaches"]}
              for r in rules if r["verdict"] == "fail"]
    causes: dict[str, int] = {}
    for i in incidents:
        for c in {f["code"] for f in i.get("findings") or []}:
            if c in CAUSES:
                causes[c] = causes.get(c, 0) + 1
    facts: dict = {
        "flight": {"lesson": session.get("name"), "pilot": session.get("pilot"),
                   "length_min": _r((session.get("flight_s") or 0) / 60, 1)},
        "sop_check": ({"strictness": evaluation.get("strictness"), "rules_met": len(met), "rules_broken": len(broken),
                       "reviewed_by_instructor": bool(evaluation.get("signed_off")),
                       "instructor_comment": evaluation.get("instructor_comment")}
                      if evaluation else "this flight was not checked against its SOP rules"),
        "rules_broken": broken,
        "rules_met": met,
        "broken_rule_moments": [
            {"rule": i["title"], "at": _mmss(i.get("t_s")), "phase": i.get("phase"), "worst": i.get("peak_text"),
             "outside_limit_s": _r(i.get("dur_s")),
             "findings": [f["text"] for f in i.get("findings") or [] if f.get("kind") != "data"]}
            for i in incidents[:MAX_INCIDENTS]],
        "most_common_causes": [{"cause": CAUSES[c], "moments": n} for c, n in sorted(causes.items(), key=lambda x: -x[1])],
    }
    if len(incidents) > MAX_INCIDENTS:
        facts["broken_rule_moments_not_listed"] = len(incidents) - MAX_INCIDENTS
    if stats:
        phases = {}
        for ph, p in (stats.get("phases") or {}).items():
            row = {}
            sc, h = p.get("scan"), p.get("handling")
            if sc:
                row["looking_outside_pct"] = round(sc["share"].get("outside", 0) * 100)
                for inst, v in (sc.get("check_every_s") or {}).items():
                    row[f"{inst}_checked_every_s"] = v
                row["longest_look_at_one_instrument_s"] = sc.get("longest_look_s")
            if h:
                row["speed_varied_by_kt"] = h.get("ias_sd")
                if h.get("alt_sd") is not None:
                    row["height_varied_by_ft"] = h.get("alt_sd")
                row["control_reversals_per_min"] = _r(sum((h.get("reversals_per_min") or {}).values()), 0)
            if row:
                phases[ph.lower()] = row
        facts["by_phase"] = phases
        if stats.get("approach"):
            a = stats["approach"]
            facts["final_approach"] = {"average_speed_kt": a.get("ias_mean"), "speed_varied_by_kt": a.get("ias_sd"),
                                       "average_sink_fpm": _r(-(a.get("vs_mean") or 0), 0) if a.get("vs_mean") is not None else None}
        if stats.get("landing"):
            ld = stats["landing"]
            facts["touchdown"] = {"sink_rate_fpm": _r(abs(ld["vs_fpm"]), 0) if ld.get("vs_fpm") is not None else None,
                                  "speed_kt": ld.get("ias"), "pitch_deg": ld.get("pitch"), "bank_deg": ld.get("bank")}
        calls = coaching_summary(stats.get("callouts") or [])
        if calls:
            facts["mavis_callouts"] = [{"callout": c["label"], "times": c["count"], "responded": c["responded"],
                                        "fixed_it": c["helped"] if c["judged"] else None,
                                        "typical_response_s": c["median_reaction_s"]} for c in calls]
        if stats.get("eye_coverage") is not None:
            facts["eye_tracker_coverage_pct"] = round(stats["eye_coverage"] * 100)
            if stats["eye_coverage"] < 0.6:
                facts["note"] = "eye tracker covered little of the flight: scan numbers are uncertain"
    if previous:
        facts["previous_flight"] = previous
    return facts


_SYSTEM = f"""You are a flight instructor writing the debrief of ONE whole training flight for the student pilot,
from measured facts about that flight.

STRICT RULES:
- Use ONLY the facts given. Every number you write must appear in the facts. Never invent times, speeds,
  heights, instruments or events.
- Exactly these four sections, each starting on its own line with the heading followed by a colon:
  {", ".join(SECTIONS)}.
- Overview: 2-3 sentences on how the flight went overall.
- What went well: 1-3 short points, each on its own line starting with "- ".
- What to work on: the 1-3 most important points, most important first, each on its own line starting with
  "- ", naming the likely reason (seeing it, acting on it, handling) where the facts show it.
- Next flight: one concrete thing to focus on.
- If there is a previous flight in the facts, say what got better or worse (only from those facts).
- About 120-220 words in all. Plain text, no markdown other than the "- " points. Calm, specific,
  encouraging. No blame."""


def build_messages(facts: dict) -> list[dict]:
    user = (f"Background for reading the facts:\n{KNOWLEDGE}\nFACTS (JSON):\n"
            f"{compact_json(facts)}\n\nWrite the flight debrief now.")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def split_sections(text: str) -> list[dict]:
    """'Overview: ...' blocks -> [{title, body}]; anything before the first heading goes under Overview."""
    out: list[dict] = []
    for line in text.replace("**", "").splitlines():
        s = line.strip().lstrip("#").strip()
        head = next((h for h in SECTIONS if s.lower().startswith(h.lower()) and s[len(h):len(h) + 1] in (":", "")), None)
        if head:
            out.append({"title": head, "body": s[len(head):].lstrip(":").strip()})
        elif s:
            if not out:
                out.append({"title": SECTIONS[0], "body": ""})
            out[-1]["body"] = (out[-1]["body"] + "\n" + s).strip()
    return out

