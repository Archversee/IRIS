"""Checkpoints ("let me try that again"): teleport back to the start of a
leg and re-fly it, and the forced-repeat gate trainee / pilot modes use.

The checkpoint snapshots themselves are taken in the tick loop (main.py) on
every PHASE_ENTER_; the dicts live there too, so session resets clear them."""
from __future__ import annotations

from coach import phrases


# ---------- training modes (visitor / trainee / pilot / exam) ----------
# Trainee + pilot gate progression with FORCE_REPEAT when a graded leg isn't
# flown to tolerance (up to MAX_ATTEMPTS). Visitor + exam never force a
# repeat, and neither does Free Flight. Coaching density / guidance style come
# from session["policy"].
GATED = {"CROSSWIND", "DOWNWIND", "BASE", "FINAL"}
MAX_ATTEMPTS = 2


def build_checkpoints(rt, state, coach, loop, event_log, _sim_now, checkpoints):
    """retry() and phase_passed(). rt is read at call time (rt.engine / rt.sim /
    rt.scorer change on a lesson switch); checkpoints is main's dict, filled
    by the tick loop."""
    async def retry(phase: str | None = None, reason_code: str = "TELEPORT"):
        """Teleport back to the start of a phase and let the trainee retry.
        reason_code selects the spoken line (TELEPORT for a request, FORCE_REPEAT
        when trainee/pilot mode is making them re-fly a leg)."""
        order = rt.engine.detector.ORDER
        want = phase if phase in order else order[1]   # generic redo -> first step, not current phase
        # walk backwards until we find a phase we actually checkpointed
        i = order.index(want)
        ckpt = None
        while i >= 0:
            ckpt = checkpoints.get(order[i])
            if ckpt:
                want = order[i]
                break
            i -= 1
        if not ckpt:
            await coach.say(phrases.render("TELEPORT_FAIL", {}))
            return
        await coach.say(phrases.render(reason_code, {}))
        t = ckpt["tel"]
        ok = True
        if ckpt.get("blob") is not None and hasattr(rt.sim, "restore_state"):
            await loop.run_in_executor(None, rt.sim.restore_state, ckpt["blob"])
        elif hasattr(rt.sim, "set_position"):
            gnd = bool(t.get("on_ground"))
            ok = await loop.run_in_executor(
                None, lambda: rt.sim.set_position(t["lat"], t["lon"], t["alt_msl"], t["hdg"],
                                                  t["ias"] if gnd else max(60.0, t["ias"]),
                                                  on_ground=gnd))
        if ok:
            rt.scorer.note_retry()
            event_log.retried(_sim_now())     # the abandoned attempt ends here; the re-fly is a new attempt
            rt.engine.load_phase(want, ckpt["circuit"])
            state["track"].append([])          # new attempt = new track segment (all attempts kept)
        else:
            await coach.say(phrases.render("TELEPORT_FAIL", {}))

    def phase_passed(phase: str, tel) -> bool:
        m = rt.engine.monitors
        spd = m.tgt_speed.get(phase)
        if spd is not None and abs(tel.ias - spd) > 15:
            return False
        if phase == "DOWNWIND" and abs(tel.alt_msl - m.pattern_alt) > 150:
            return False
        if phase == "FINAL" and rt.engine.geom is not None and abs(rt.engine.geom.cross) > 120:
            return False
        return True

    return retry, phase_passed
