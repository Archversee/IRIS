# External Walk-Around, spoken checklist draft

Draft content for `lesson00_preflight` (currently a catalog stub, see root `CLAUDE.md`).
Sourced from BAA Training + ThrustFlight C172 preflight guides (standard counterclockwise
walk), cross-checked against the earlier Google-sourced notes already in `CLAUDE.md`.
**Verify against the actual aircraft's POH before this becomes shipped coaching content**,
this is a generic C172/G1000 procedure, not this specific tail number's.

Each checkpoint = one spoken line (what the coach says) + what the trainee should be
looking for. Written for the checkpoint-index lesson engine described in `CLAUDE.md`
(not the flight-phase engine), one `advance()` per checkpoint.

## 0. Before you step outside (cabin check)

> "Before we head out, let's run the cabin check. Documents on board? Airworthiness
> certificate, registration, and the POH should all be here."

- Remove the control lock if fitted.
- Master switch **on** briefly: check exterior lights (nav, beacon, strobe, landing
  light) and pitot heat, confirm the PFD powers up and fuel gauges register, then
  **off** again before you go outside.
- Fuel selector to **BOTH**.
- Flaps down (gives you access to check them, and matches the walk-around photos).

## 1. Left wing root, tie-down

> "Starting at the left wing. First, remove the tie-down if it's still on."

## 2. Left wing

> "Run your hand along the leading edge, feel for dents or damage. Check the aileron
> moves freely and is securely hinged. Then the flap, same thing, plus the rails and
> actuator."

- Stall warning vane/opening near the wingtip, should move freely, not be blocked.
- Fuel sump: drain a sample from the left tank sump, check for water (it'll sit as a
  separate bead in the sample) or sediment.
- Fuel cap secure.

## 3. Left main gear

> "Down to the left wheel. Check tire inflation and wear, brake pad and disc, and the
> strut, it should show the right amount of compression, not collapsed or overinflated."

## 4. Nose

> "Now the nose. Cowling secure, all the screws in place. Propeller and spinner,
> look for nicks or cracks, don't just glance, run your eyes right along both blades."

- Oil: dipstick reads at least 6 quarts (POH minimum for dispatch, verify against
  this aircraft's actual POH figure).
- Air filter and intakes clear of obstruction.
- Nose tire condition and strut/suspension inflation.

## 5. Right main gear

> "Same checks on the right wheel. Tire, brake, strut."

## 6. Right wing

> "Right wing, working back out to the tip. Fuel sump first, drain and check for
> water. Then leading edge, aileron, flap, and remove the tie-down."

## 7. Right fuselage / cabin

> "Cabin door and baggage door. Both closed and secured. Check the fuselage skin
> along here for anything unusual."

## 8. Empennage (tail)

> "Down to the tail. Horizontal stabilizer and elevator, check the surface, then
> move the elevator through its full range, it should be free and not sticky.
> Same for the vertical stabilizer and rudder. Check the static wicks are all
> present, they're the thin rods on the trailing edges."

- Tail tie-down removed.

## 9. Antennas

> "Quick look along the belly and tail for the antennas, make sure none are bent
> or hanging loose."

## 10. Final check

> "Last thing. Look at the ground under the aircraft for any fluid, and give the
> whole airplane one more look before we call it good. If anything felt off back
> there, or you're not sure about something, that's exactly the right instinct,
> always ask rather than guess."

---

**Open design question for the checkpoint engine (see `CLAUDE.md`):** does the trainee
advance by tapping "next" per checkpoint, or does the coach ask a quick verification
question at each stop (e.g. "how many quarts minimum?") before advancing? The script
above supports either, the spoken lines work standalone, and short Q&A could be
layered on top per checkpoint later without rewriting this draft.
