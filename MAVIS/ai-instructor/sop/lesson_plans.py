"""Which SOP / handbook rules each curriculum lesson checks.

Every flying lesson has its own rule list, set up by an instructor before it's
flown (IRIS: Tools > SOP Rules > Rules per lesson, or on the lesson's start
screen):

  * RECOMMENDED: a rule is suggested for a lesson when it can actually happen
    in it -- its phases (FINAL, FLARE_TOUCHDOWN, ...; set when the rule was
    drafted from the SOP / handbook) are flown in that lesson, or it applies
    in every phase (e.g. lookout). The lesson's own `phases:` list in
    lessons/<id>.yaml is what's flown; a lesson with no circuit phases
    (steep turns, slow flight, ...) is recommended only the whole-flight rules.
    Each rule carries the reason it was or wasn't suggested.
  * The instructor then adds or removes rules. Only those changes are stored
    (`added` / `removed` rule ids), so a rule added to an SOP later shows up
    as recommended by itself, and "Reset" goes back to the recommendation.
  * `checks_on`: a lesson's checks can be switched off altogether (no live
    callouts, nothing in Review). Free Flight isn't a lesson: it's always
    checked against every active rule.

Rules NOT on a lesson's list are not checked at all in that lesson: not live,
and not in the flight's results.

Stored in Postgres next to the rules (table sop_lesson_plans, created here if
missing; also in db/schema_sop_rules.sql).
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from core.lesson_catalog import current_id, old_ids

from .schema import APP_ROOT, LESSON_TYPES, aircraft_profiles, resolve_ref

log = logging.getLogger(__name__)

SCHEMA = """
create table if not exists sop_lesson_plans (
    lesson_id   text primary key,                -- lessons/<id>.yaml
    checks_on   boolean not null default true,   -- false: this lesson isn't checked against any rule
    added       bigint[] not null default '{}',  -- flight_rules.id the instructor added on top of the recommendation
    removed     bigint[] not null default '{}',  -- ... and took off it
    updated_by  text,
    updated_at  timestamptz not null default now()
)"""

# not flown: the briefing/debrief bookends every lesson has
_NOT_FLOWN = {"BRIEFING", "DEBRIEF"}
PHASE_WORDS = {
    "PRE_TAKEOFF": "before take-off", "TAKEOFF_ROLL": "take-off roll", "CLIMB_OUT": "climb-out",
    "CROSSWIND": "crosswind", "DOWNWIND": "downwind", "BASE": "base", "FINAL": "final",
    "FLARE_TOUCHDOWN": "flare and touchdown", "ROLLOUT": "roll-out", "GO_AROUND": "go-around",
}
_CAPTURE_NEEDS = {"liftoff": "TAKEOFF_ROLL", "touchdown": "FLARE_TOUCHDOWN"}


def checked_by(rule: dict) -> str:
    """Who judges it: 'mavis' (flight data), 'eyes' (eye tracker, after the
    flight) or 'instructor' (can't be measured: radio calls, checklists)."""
    k = rule.get("kind")
    return "instructor" if k == "unobservable" else "eyes" if k == "gaze" else "mavis"


def missing_speed(rule: dict, aircraft_id: str | None) -> str | None:
    """A rule written against an aircraft speed ('1.3 VSO') that this aircraft's
    profile doesn't give is never loaded for a flight: say which speed."""
    if not rule.get("ref_speed"):
        return None
    speeds = next((a["profile"].get("v_speeds") or {} for a in aircraft_profiles() if a["id"] == aircraft_id), {})
    return None if resolve_ref(rule, speeds) is not None else str(rule.get("ref_speed"))


def words(phases) -> str:
    return ", ".join(PHASE_WORDS.get(p, p.lower().replace("_", " ")) for p in phases)


# ---------------------------------------------------------------- lessons
def load_lesson(lesson_id: str) -> dict:
    p = APP_ROOT / "lessons" / f"{lesson_id}.yaml"
    if not p.is_file():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("lesson %s unreadable: %s", lesson_id, e)
        return {}


def lesson_type(lesson: dict) -> str:
    lt = lesson.get("rules_lesson_type")
    return lt if lt in LESSON_TYPES else "normal"


def flown_phases(lesson: dict) -> list[str]:
    return [p for p in (lesson.get("phases") or []) if p not in _NOT_FLOWN]


def flying_lessons(catalog: dict) -> list[dict]:
    """The curriculum's flying lessons (not the reading ones), in catalog order."""
    out = []
    for mod in catalog.get("modules", []):
        for les in mod.get("lessons", []):
            if les.get("kind") == "reading" or not les.get("available", True):
                continue
            if not (APP_ROOT / "lessons" / f"{les.get('id')}.yaml").is_file():
                continue
            out.append({"id": les["id"], "title": les.get("title") or les["id"],
                        "subtitle": les.get("subtitle"), "module": mod.get("title") or mod.get("id")})
    return out


# ---------------------------------------------------------------- recommendation
def recommend(rule: dict, phases: list[str]) -> tuple[bool, str]:
    """(recommended?, why) for one rule in a lesson that flies `phases`."""
    rp = [p for p in (rule.get("phases") or []) if p not in _NOT_FLOWN]
    need = [x for x in (rule.get("gate_phase"), _CAPTURE_NEEDS.get(rule.get("capture_at"))) if x]
    if not phases:                       # not a circuit lesson: only whole-flight rules can be placed
        if rp or need:
            return False, f"For {words(rp or need)}, which this lesson doesn't fly as a circuit"
        return True, "Applies the whole flight"
    missing = [p for p in need if p not in phases]
    if missing:
        return False, f"Needs {words(missing)}, which this lesson doesn't fly"
    if not rp:
        return True, "Applies the whole flight"
    hit = [p for p in rp if p in phases]
    if hit:
        return True, f"For {words(hit)}, flown in this lesson"
    return False, f"For {words(rp)}, not flown in this lesson"


# ---------------------------------------------------------------- store
class LessonPlans:
    def __init__(self, store):
        self.store = store
        self._ready = False

    async def _ensure(self, c) -> None:
        if not self._ready:
            await c.execute(SCHEMA)
            self._ready = True

    async def _row(self, c, lesson_id: str) -> dict:
        await self._ensure(c)
        # saved before the lesson was renamed: under an old id, until next edited
        r = await c.fetchrow("select * from sop_lesson_plans where lesson_id = any($1::text[]) "
                             "order by (lesson_id = $2) desc, updated_at desc limit 1",
                             [lesson_id, *old_ids(lesson_id)], lesson_id)
        return {"checks_on": True, "added": [], "removed": [], "updated_by": None, "updated_at": None} if r is None \
            else {"checks_on": r["checks_on"], "added": list(r["added"]), "removed": list(r["removed"]),
                  "updated_by": r["updated_by"], "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None}

    async def rows(self) -> dict[str, dict]:
        async with self.store.acquire() as c:
            await self._ensure(c)
            out = {}
            # oldest first, and a renamed lesson's old row before its own: the last one wins
            rows = await c.fetch("select * from sop_lesson_plans order by updated_at")
            for r in sorted(rows, key=lambda r: current_id(r["lesson_id"]) == r["lesson_id"]):
                out[current_id(r["lesson_id"])] = {"checks_on": r["checks_on"], "added": list(r["added"]),
                                       "removed": list(r["removed"]), "updated_by": r["updated_by"]}
            return out

    async def plan(self, lesson_id: str, aircraft_id: str | None, row: dict | None = None) -> dict:
        """The lesson's list: every rule that could be used in it (the aircraft's
        active rules + the active training handbooks, of the lesson's type),
        each with `recommended` + `why`, and `included` (= on the list)."""
        lesson = load_lesson(lesson_id)
        lt, phases = lesson_type(lesson), flown_phases(lesson)
        if row is None:
            async with self.store.acquire() as c:
                row = await self._row(c, lesson_id)
        act = await self.store.active_rules(aircraft_id, lt) or {"rules": [], "layers": []}
        added, removed = set(row["added"]), set(row["removed"])
        rules = []
        for r in act["rules"]:
            rec, why = recommend(r, phases)
            rid = int(r["id"])
            no_speed = missing_speed(r, aircraft_id)
            if no_speed:                 # never loaded for this aircraft: can't be on any list
                rec, why = False, f"Can't be checked: this aircraft's profile has no {no_speed.upper()} speed"
            inc = not no_speed and ((rec and rid not in removed) or rid in added)
            rules.append({**r, "recommended": rec, "why": why, "included": inc, "unavailable": bool(no_speed),
                          "checked_by": checked_by(r),
                          "changed": not no_speed and ((rid in added and not rec) or (rid in removed and rec))})
        return {"lesson_id": lesson_id, "lesson_name": lesson.get("name") or lesson_id, "lesson_type": lt,
                "phases": phases, "phase_words": words(phases) if phases else None,
                "aircraft_id": aircraft_id, "layers": act.get("layers") or [],
                "checks_on": row["checks_on"], "updated_by": row["updated_by"], "updated_at": row.get("updated_at"),
                "n_included": sum(1 for r in rules if r["included"]),
                # who judges the rules on the list: MAVIS from flight data / the eye tracker, or the instructor
                "n_mavis": sum(1 for r in rules if r["included"] and r["checked_by"] == "mavis"),
                "n_eyes": sum(1 for r in rules if r["included"] and r["checked_by"] == "eyes"),
                "n_instructor": sum(1 for r in rules if r["included"] and r["checked_by"] == "instructor"),
                "n_recommended": sum(1 for r in rules if r["recommended"]),
                "n_changed": sum(1 for r in rules if r["changed"]),
                "rules": rules}

    async def update(self, lesson_id: str, *, by: str | None = None, checks_on: bool | None = None,
                     include: list[int] | None = None, exclude: list[int] | None = None,
                     reset: bool = False, aircraft_id: str | None = None) -> dict:
        """include / exclude: rule ids to put on / take off the list. Stored as
        differences from the recommendation; reset drops them all."""
        lesson = load_lesson(lesson_id)
        if not lesson:
            raise LookupError(f"No lesson '{lesson_id}'")
        phases = flown_phases(lesson)
        recommended = {}
        if include or exclude:
            act = await self.store.active_rules(aircraft_id, lesson_type(lesson)) or {"rules": []}
            # rules of other aircraft (not in this list) keep whatever was chosen for them
            recommended = {int(r["id"]): recommend(r, phases)[0] for r in act["rules"]}
        async with self.store.acquire() as c:
            async with c.transaction():
                row = await self._row(c, lesson_id)
                added, removed = set(row["added"]), set(row["removed"])
                if reset:
                    added, removed = set(), set()
                for rid in include or []:
                    rid = int(rid)
                    removed.discard(rid)
                    if not recommended.get(rid, False):
                        added.add(rid)
                for rid in exclude or []:
                    rid = int(rid)
                    added.discard(rid)
                    if recommended.get(rid, True):
                        removed.add(rid)
                on = row["checks_on"] if checks_on is None else bool(checks_on)
                await c.execute("""
                    insert into sop_lesson_plans (lesson_id, checks_on, added, removed, updated_by, updated_at)
                    values ($1, $2, $3, $4, $5, now())
                    on conflict (lesson_id) do update set checks_on=$2, added=$3, removed=$4,
                        updated_by=$5, updated_at=now()""",
                                lesson_id, on, sorted(added), sorted(removed), by)
                row = await self._row(c, lesson_id)
        log.info("rules for lesson %s updated by %s: checks %s, +%d / -%d vs recommended", lesson_id, by or "?",
                 "on" if row["checks_on"] else "OFF", len(row["added"]), len(row["removed"]))
        return await self.plan(lesson_id, aircraft_id, row)

    async def summaries(self, catalog: dict, aircraft_id: str | None) -> list[dict]:
        """Every flying lesson with how many rules it checks (for the lesson picker)."""
        rows = await self.rows()
        out = []
        for les in flying_lessons(catalog):
            row = rows.get(les["id"]) or {"checks_on": True, "added": [], "removed": [], "updated_by": None}
            p = await self.plan(les["id"], aircraft_id, row)
            out.append({**les, "checks_on": p["checks_on"], "n_included": p["n_included"],
                        "n_mavis": p["n_mavis"], "n_eyes": p["n_eyes"], "n_instructor": p["n_instructor"],
                        "n_recommended": p["n_recommended"], "n_changed": p["n_changed"],
                        "phase_words": p["phase_words"]})
        return out

    async def rules_for_flight(self, lesson_id: str, rules: list[dict]) -> tuple[list[dict] | None, dict]:
        """Filter the rules loaded for a lesson's flight down to its list.
        -> (rules, info); rules None = this lesson's checks are switched off."""
        lesson = load_lesson(lesson_id)
        phases = flown_phases(lesson)
        async with self.store.acquire() as c:
            row = await self._row(c, lesson_id)
        if not row["checks_on"]:
            return None, {"checks_on": False}
        added, removed = set(row["added"]), set(row["removed"])
        keep = [r for r in rules
                if (recommend(r, phases)[0] and int(r["id"]) not in removed) or int(r["id"]) in added]
        return keep, {"checks_on": True, "n_on_list": len(keep), "n_left_off": len(rules) - len(keep),
                      "customised": bool(added or removed)}
