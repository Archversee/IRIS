"""Instructor admin actions (PIN unlock, instructor accounts, reference runs).

Web actions only: they edit the session / state and the instructor and
reference registries; nothing here runs in the tick loop."""
from __future__ import annotations


def build_instructor_admin(rt):
    """Defines the instructor_* actions and publishes them on rt."""
    def _sync_instructors_state() -> None:
        rt.state["instructors"] = rt.instructors.list_admin() if rt.session.get("instructor_unlocked") else []

    async def instructor_unlock(pin: str):
        inst = rt.instructors.verify_pin(pin)
        if not inst:
            return False, None
        rt.session["instructor_unlocked"] = True
        rt.session["instructor"] = inst
        rt.state["instructor_unlocked"] = True
        rt.state["instructor"] = inst
        _sync_instructors_state()
        rt._sync_refs_to_state()
        return True, inst

    async def instructor_lock():
        rt.session["instructor_unlocked"] = False
        rt.session.pop("instructor", None)
        rt.state["instructor_unlocked"] = False
        rt.state["instructor"] = None
        rt.state["instructors"] = []

    async def instructor_add(callsign: str, pin: str) -> bool:
        if not rt.session.get("instructor_unlocked"):
            return False
        if not rt.instructors.add(callsign, pin):
            return False
        _sync_instructors_state()
        return True

    async def instructor_update(inst_id: str, callsign: str = "", pin: str = "") -> bool:
        if not rt.session.get("instructor_unlocked"):
            return False
        kw = {}
        if callsign.strip():
            kw["callsign"] = callsign
        if pin.strip():
            kw["pin"] = pin
        if not kw:
            return False
        if not rt.instructors.update(inst_id, **kw):
            return False
        if rt.session.get("instructor", {}).get("id") == inst_id:
            row = next((r for r in rt.instructors.list_admin() if r["id"] == inst_id), None)
            if row:
                rt.session["instructor"] = {"id": row["id"], "callsign": row["callsign"]}
                rt.state["instructor"] = rt.session["instructor"]
        _sync_instructors_state()
        return True

    async def instructor_remove(inst_id: str) -> bool:
        if not rt.session.get("instructor_unlocked"):
            return False
        if rt.session.get("instructor", {}).get("id") == inst_id:
            return False
        if not rt.instructors.delete(inst_id):
            return False
        _sync_instructors_state()
        return True

    async def instructor_rename(run_id: str, display_name: str) -> bool:
        if not rt.session.get("instructor_unlocked"):
            return False
        if not rt.refs.rename(run_id, display_name):
            return False
        rt._sync_refs_to_state()
        if rt.state["reference"].get("id") == run_id:
            rt.state["reference"]["name"] = display_name.strip()[:64]
        return True

    async def instructor_delete(run_id: str) -> bool:
        if not rt.session.get("instructor_unlocked"):
            return False
        if not rt.refs.delete(run_id):
            return False
        rt._sync_refs_to_state()
        if rt.state["reference"].get("id") == run_id:
            rt._apply_reference("planned")
        return True

    rt.instructor_add = instructor_add
    rt.instructor_delete = instructor_delete
    rt.instructor_lock = instructor_lock
    rt.instructor_remove = instructor_remove
    rt.instructor_rename = instructor_rename
    rt.instructor_unlock = instructor_unlock
    rt.instructor_update = instructor_update
