# SOP → flight rules

An instructor uploads an aircraft's Standard Operating Procedure (PDF). MAVIS reads it and a **local** LLM drafts machine-checkable flight rules. The instructor reviews and edits them, then saves them to the database as that aircraft's active rule set. Saved rules can be edited at any time, and every change is logged.

The same pages also take **training handbooks** — the FAA handbooks, or a flight school's own. Their rules (circuit procedure, scan path) apply to **every aircraft** and are used together with each aircraft's own rules. See [Training handbooks](#training-handbooks-faa-or-the-schools-own).

Everything runs on the sim laptop: PyMuPDF and Tesseract read the PDF, and Ollama drafts the rules. Nothing leaves the machine.

## Where it lives

The pages are in the IRIS React app (the dashboard at `http://localhost:3000`), under **IRIS → SOP Upload / SOP Rules** in the sidebar. They are instructor tools: only instructors and admins see them, and a trainee who opens their address is sent to the home page.

| Page | What it's for |
|---|---|
| **Upload SOP or handbook** — `/sop/new` | Choose *Aircraft POH / SOP* or *Training handbook* → upload → OCR / LLM → review and edit the drafted rules → **Save**. Or drop a rules file exported earlier (`.json`) to review and save it without a PDF or an LLM run |
| **SOP Rules** — `/sop/rules` | Pick an aircraft (or **Training handbooks**), then browse, edit, add, delete, or switch off its saved rules; see the change history; switch between rule sets |

The pages call MAVIS's `/sop/*` API through the `/mavis` proxy prefix. There is no PIN on these pages. The **Editing as** box in the page header records who made each change; it is remembered per browser.

## Upload SOP (`/sop/new`)

1. **Upload.** Drop the PDF. Status chips show whether the local LLM, OCR and the database are available, and whether a lesson is in progress (uploading waits until it ends). A **rules file (.json)** made with Export → JSON can be dropped instead of a PDF — see [Import a rules file](#import-a-rules-file).
   - **LLM that drafts the rules** (card at the top of the page): one button per model installed in Ollama; the coach's model is marked. Click one to switch — it is used for every run from then on and kept across restarts. See [Switching the model](#switching-the-model).
2. **Choose pages** (documents over 40 pages). MAVIS reads the text of every page, without OCR, which is fast. It then detects the POH sections from the running headers and footers ("SECTION 2 / LIMITATIONS", page labels like `2-4`).
   - It suggests **Section 2 Limitations** and **Section 4 Normal Procedures**. Those hold the rules the simulator can check. The optional-equipment supplements (autopilot, GPS, transponder…) only produce noise.
   - Tick sections, or type ranges (`13-20, 34-63`), preview a page, and choose the **aircraft**. The aircraft is given to the LLM and used for the circuit check below.
   - **LLM model** (shown when Ollama has more than one model installed): which local model drafts this run only. The default is the one chosen on the upload page; choosing another here is how to compare models on the same pages without editing `config.yaml`. The draft, the saved rule set and every export record the model used.
   - Documents of 40 pages or fewer skip this step and are read whole.
3. **Processing.** A progress bar steps through: OCR the chosen scanned pages → draft rules → check rules. You can leave the page; processing continues on the MAVIS machine. The draft is listed under **Unsaved drafts** when you come back.
4. **Review and edit.** Nothing is in the database yet. The draft is a local file in `MAVIS/ai-instructor/data/sop/drafts/`, and every edit is autosaved to it. A refresh, a closed tab or a MAVIS restart loses nothing.
   - The left side lists the rules by SOP page, each with a plain-English summary (*"Hold indicated airspeed at 74 kt +10 / −5 kt during Climb out."*) and its **tags**. Filters: **Needs fixing**, **Warnings**, **Set aside**, plus a tag filter.
   - The right side has the rule editor and the SOP page the rule came from. You can show the page image, with the quote highlighted, or the extracted text.
   - Untick a rule to **set it aside**: it is still saved with the rule set, but not used in lessons (see [Set-aside rules](#set-aside-rules)). **+ Add rule** adds your own. **Export** downloads the draft as it is now (CSV or JSON), set-aside rules included. **Run LLM again** re-drafts from the same pages with the same model; **Change pages or model** re-drafts from different pages and/or with another installed model. Both keep the rules you added by hand.
   - Keyboard: `J`/`K` move between rules, `X` includes or sets aside the current rule.
5. **Save.** Choose the aircraft and a rule set name, then press **Save N rules**. One transaction:
   - writes the SOP document and pages, the rule set, its rules — the ones in use **and** the set-aside ones — and a `create` history entry per rule;
   - makes the set **active** for that aircraft (the previously active set is kept, inactive);
   - deletes the draft file.

   Pressing **Save** again while a save is still running is refused ("already being saved"), so a rule set is never written twice; the proxy waits up to 3 minutes for SOP saves (30 s for everything else).

   **Save** stays disabled until an aircraft is chosen and nothing included needs fixing. A rule that needs fixing can always be set aside instead: it is saved as it is and can be fixed later. A draft whose rules are **all** set aside can be saved too (typical for scan-path rules that still need thresholds): nothing is used in lessons until rules are added back.

### Import a rules file

Drop a `.json` file made with **Export → JSON** (from a saved rule set or from a draft) on the upload box. It opens as a draft straight away — no PDF is read and the LLM is not run. Use it to move rules from one machine to another, to restore a backup, or to load a file that was corrected by hand.

- Everything in the file comes back: each rule's fields, tags, lesson types, aircraft-speed reference, quote, page and note; whether it was **in use**, **switched off** or **set aside** (with its reason); the rule-set name, aircraft or handbook, the model and prompt version that drafted it and the pages it was drafted from.
- Every rule is **checked again** with the same checks as an LLM draft. A rule marked "in use" that doesn't pass shows as **Needs fixing**; fix it or set it aside. Nothing reaches the database until **Save**.
- **The PDF.** Exports carry the PDF's fingerprint (`source_sha256`). If that PDF is on this machine — in another draft, or saved with an earlier rule set — the draft is linked to it: quotes are verified, the page viewer works and the LLM can be run again. Older exports without a fingerprint are matched by file name, and only if most of their quotes really are in that PDF. Otherwise the rules keep their quote and page number as text, there is no page viewer, and **Run LLM again** is hidden; a quote that can't be checked never blocks an imported rule.
- A rule that was set aside as **"quote not found"** when the file was exported, and whose quote *is* found in the linked PDF now (see shortened quotes above), stays set aside but says so, and the banner counts them: check them under **Set aside** and add back the ones you want. This recovers such rules from an existing export without running the LLM again.
- A file exported before lesson types existed has none: its rules tagged `emergency` become *Emergency* rules, the rest *Normal circuit*.
- A share of time written by hand as a percentage (`"value": 90`) is read as 90 %; stored shares are 0–1.
- Unknown aircraft profile → the reviewer chooses the aircraft. Entries without a rule kind are skipped and counted in the banner. Limit: 20 MB / 2000 rules.

The file format is `mavis-sop-rules/1`: `{"format", "exported_at", "rule_set": {...}, "units": {...}, "rules": [...]}` with the same field names as the `flight_rules` table. `status`, `summary` and `unit` are for readers; on import `status` / `set_aside` / `enabled` decide in use / switched off / set aside.

### What "needs fixing" means

The LLM only *drafts*. Every rule is checked deterministically (`sop/validate.py`) when it's drafted, on every autosave, and again at Save.

A rule **needs fixing** (it can't be saved while it's included) if:

- its **source quote can't be found** in the SOP text (below 85 % match). These are set aside automatically; this is how made-up rules are caught;
  - A quote the model **shortened with "..."** ("NORMAL TAKEOFF ... 5. Climb Speed -- 70-80 KIAS.") is found when **each piece** is on the same page (or the page after): every piece has to match, only a short heading-like piece may match loosely (OCR writes "NORMAL TAK EOFF"), and such pieces may be at most 30 % of the quote. An invented piece or number still fails the quote. `qwen3.6:27b` shortens more than a quarter of its quotes this way; in its first three runs 20 rules were set aside as "quote not found" for this reason (12 in the C172 POH, rotation speed and climb speed among them). Dot leaders in tables ("Normal Climb Out ........ 75-85 KIAS") are not treated as "...". The page image highlights each piece.
  - **Speed on long documents.** Every autosave and every Save re-checks each rule's quote. The page a rule names and its two neighbours are compared word by word; the rest of the document is only searched on pages that could hold the quote at all (a quote word that isn't on a page can't be matched there, which is known from the page's set of words in microseconds), and each page's normalised text is kept between checks. Before this, a 400-page handbook with a dozen shortened quotes took about 40 s per re-check: the proxy gave up after 30 s and the page said "mavis backend took too long to answer", although MAVIS finished the save a moment later. It now takes well under a second.
- its unit doesn't fit what it measures (e.g. feet for airspeed), or a value is physically implausible;
- its type is missing a value: a band without a target, a limit without a min or max, and so on. (A target that only lacks a **tolerance**, or a scan rule that only lacks its **threshold**, is not "broken": after an LLM run it is set aside automatically, waiting for the instructor's number — with the standard FAA value pre-filled where the FAA publishes one, see [FAA suggestions](#faa-suggestions-for-missing-numbers).)

A rule also **needs fixing** if its numbers appear in the quote only as a quantity the simulator can't measure, e.g. "Oil Pressure, Minimum: 20 PSI" turned into "RPM ≥ 20". The units recognised are PSI, °F/°C, gallons/GPH, lbs, in.Hg, amps/volts, MHz, hours and NM.

Rules are **set aside automatically**, each with its reason, when a deterministic check says the LLM probably got it wrong or it doesn't belong in a normal circuit lesson. Nothing is deleted — a set-aside rule is saved with the set and can be added back:

| Check | Example it catches |
|---|---|
| The quote isn't in the document | a made-up rule |
| A correctly flown circuit would break it (normal-circuit rules only) | the magneto-check RPM as an RPM limit; "500–1000 fpm on final" in an aircraft whose normal final is flatter |
| A number in the rule is not in the quoted text | an invented limit; a number copied from another line |
| The rule points the opposite way from its quote | "Throttle -- 1000 RPM **or LESS**" drafted as a *minimum* of 1000 RPM; a scan rule: "each area should be observed for **at least** 1 second" drafted as "don't look outside for longer than 1 s" |
| With the FAA value filled in, it repeats a rule already in use | "final approach at the recommended approach speed" + the FAA's +10 / −5 kt, next to the book's own "within +10/−5 KIAS of the recommended landing speed" |
| The quote is about something that isn't an operating limit | manoeuvring speed ("abrupt use of the controls is prohibited above 105 KIAS") as a never-exceed speed; demonstrated crosswind as an airspeed; "a safe flap retraction speed of 60 KIAS" or a short-field "obstacle clearance speed" as a *maximum* speed |
| The quote is a list of common errors | "The most common errors … include: failure to clear the area" turned into a rule |
| The LLM gave one number without saying what it is | "Climb Speed -- 60 KIAS" as a limit with no min or max: kept as a target, waiting for a tolerance |
| A scan-path rule has no threshold because the book gives none | "avoid fixation": waits for the instructor's number |
| A target has no tolerance because the document gives none | "Best rate-of-climb 74 KIAS", "Airspeed -- 68 KIAS": waits for the instructor's tolerance. (These used to be *Needs fixing* and blocked Save; a POH run with emergency procedures produced eleven of them) |
| The title is one of the prompt's own examples but the quote is about something else | "Clear the area before turning" quoting a paragraph about pattern speeds |
| A handbook speed limit above every aircraft's never-exceed speed | "no greater than 200 knots in the traffic pattern": true, but it can never be broken in a C172 |

Two things are corrected rather than set aside, with a note on the rule: a range the model flattened to one number, and a condition that is always true — "flaps 0° to 10°: 110 KIAS" drafted as *while flaps ≥ 0°* would apply the flap speed with the flaps up, so it becomes *while flaps > 0°*.

**Duplicates** (the same limit from a table and from a summary page) are merged, but only within one kind of lesson: "Climb Speed -- 70-80 KIAS" (normal takeoff) and "descent at 70-80 KIAS" (emergency descent through clouds) have the same numbers and are different rules.

Emergency procedures are **not** set aside any more: they are kept in use with the lesson type *Emergency* and are never loaded into a normal circuit lesson (see [Lesson types](#lesson-types)).

Repairs made without setting the rule aside:

- **phases a correct circuit rules out are removed**: "pattern altitude 1,000 ft" drafted for Downwind, Base *and* Final keeps Downwind (you descend on the other two). The rule says which phases were removed and why;
- a target whose text says **"or LESS" / "or MORE"** becomes a maximum / minimum; a single target whose text gives a **range** ("approach at 65 to 75 KIAS", "500 to 800 ft/min") becomes that range;
- "use the manufacturer's recommended airspeed, **or 1.3 VSO if there is none**" is measured from each aircraft's approach speed, not from 1.3 × VSO;
- a scan-path number must be in the text **as a percentage or as seconds**: "a height equal to 1/10 the span" does not make a 10 % rule, and the number is removed;

- a limit with the **same minimum and maximum** becomes a target (it then needs a tolerance); a "target" that is really a **range** ("generally 500–1000 fpm") becomes a limit;
- fields the rule's type doesn't use are **emptied** (the LLM has to write every field, so it leaves a stray `value` on a limit), which also lets the same limit drafted twice be recognised as **one rule**;
- a scan-path **number that isn't in the quoted text is removed** (an invented "50 %" is worse than none); a share written as `90` is read as 90 %;
- a scan-path rule given as a **share of time with no percentage in the book** ("refer to the attitude indicator") becomes the time-based check that says the same thing — *look at it at least every N s* / *no single look longer than N s* — because that is a number an instructor can set;
- an airspeed target the LLM left empty because **the text names an aircraft speed** ("within +10/−5 KIAS of the recommended landing speed") is turned into a rule measured from that speed, with the tolerance read from the text (see [Aircraft-relative speeds](#aircraft-relative-speeds));
- two "not observable" or two scan rules resting on the same passage are merged;
- a candidate that quotes one of the prompt's own example sentences, when the document doesn't contain it, is dropped.

Quotes from **tables** are matched more tolerantly: a PDF often gives a table's cells in a different order than a person reads them, so a quote counts as found when all its words and numbers are close together on the page, in any order. Such a rule is kept with a warning to compare it with the page image.

The **circuit check** (`sop/profile.py`): MAVIS builds the expected circuit from the lesson file and the aircraft profile:

- speeds per phase (rotate at Vr, climb at Vy, downwind / base / final speeds);
- pattern height;
- flap settings per phase;
- typical engine RPM / throttle / vertical-speed / bank ranges, overridable per aircraft under `nominal:` in its profile YAML.

A target **without a tolerance** is still compared, using a generous provisional tolerance (±10 kt, ±200 ft, ±300 ft/min, ±200 RPM): "Throttle -- 1800 RPM" (the magneto check) drafted as a pre-takeoff target, or "1.4 VSO on base" (56 kt in a C172 that flies base at 75), are set aside with the reason instead of waiting for a tolerance that could never make them right.

The comparison allows a little slack (5 kt, 100 ft, 100 ft/min, 5° of bank, 100 RPM): the expected circuit is a sketch — "climb at exactly 74 kt" — so "Normal climb 75–85 KIAS" is not treated as contradicted by it.

This catches rules meant for another situation, e.g. the engine-out glide speed as a climb limit, the 1800 RPM magneto check as an RPM limit, or autopilot-only speeds. You can re-include a set-aside rule: it then shows as a warning, not a block. The same check runs on edits to saved rules and is shown as a warning after saving.

**What may be missing.** After a run the page lists what a document of that kind normally gives but **no drafted rule covers** — for a POH: never-exceed speed, flap speed limit, maximum RPM, rotation speed, climb speed, approach speed, the before-takeoff and before-landing checklists; for the Airplane Flying Handbook: pattern altitude, time outside, airspeed on final; for the Instrument Flying Handbook: fixation and omission. The local model sometimes simply misses a passage (one POH run produced 37 emergency rules and no climb speed); the warning says so, and the rule can be added by hand or the run repeated.

A **warning** doesn't block saving. Warnings are shown for:

- a number that doesn't appear in the quoted text;
- a quote that isn't word for word;
- a rule from an OCR'd page (compare it with the page image);
- a corrected page number;
- dropped phases.

### Set-aside rules

A rule is set aside by the reviewer (untick it), or automatically (see the table above). Set-aside rules used to be dropped at Save; they are now **kept with the rule set**:

- they are **never used in a lesson** and don't count as rules of the set;
- each keeps the **reason** it was set aside (what blocks it, the circuit check, or "set aside by the reviewer");
- on **SOP Rules**, the **Set aside** filter lists them. They can be edited and saved even while incomplete or broken — a band without a tolerance, a scan rule without a threshold, a quote that isn't in the document;
- **Add back to the rule set** runs the full check first and is refused, with the reason, until the rule passes. Edits and adding back can be done in one step;
- a rule in use can be **Set aside** at any time (instead of deleting it or switching it off);
- they are included in every **export**, marked in a `status` column (*In use* / *Switched off* / *Set aside*) with a `set_aside_reason`, listed after the rules in use. An export of a **draft** also uses *Needs fixing* (with the reason in `needs_fixing`) for rules that are ticked but don't pass the checks yet.

### FAA suggestions for missing numbers

A POH says "Best rate of climb 74 KIAS" and a handbook says "avoid fixating on one instrument": the rule is clear, but the document gives **no tolerance / no threshold**, and a rule can't be checked without one. Such rules are set aside. For the common cases the FAA publishes the number, and it is now **pre-filled** on the set-aside rule (`sop/suggest.py`):

| Rule lacks | In | Suggested | From |
|---|---|---|---|
| tolerance on a **climb speed** (VY/VX, or takeoff-roll / climb-out phases) | normal lessons | +10 / −5 kt | Private Pilot ACS (FAA-S-ACS-6C) PA.IV.A Normal Takeoff and Climb |
| tolerance on an **approach speed** (aircraft approach speed, or final / flare phases) | normal lessons | +10 / −5 kt | ACS PA.IV.B Normal Approach and Landing |
| tolerance on any other **airspeed** | normal lessons | ±10 kt | ACS PA.III.B Traffic Patterns |
| tolerance on an **altitude** | normal lessons | ±100 ft | ACS PA.III.B Traffic Patterns (pattern altitude) |
| tolerance on an airspeed / altitude / heading | instrument lessons | ±10 kt / ±200 ft / ±20° | ACS PA.VIII Basic Instrument Maneuvers |
| tolerance on a **glide / engine-failure speed** | emergency lessons | ±10 kt | ACS PA.IX.B Emergency Approach and Landing (best glide) |
| tolerance on an **emergency-descent speed** | emergency lessons | +0 / −10 kt | ACS PA.IX.A Emergency Descent |
| tolerance on another emergency speed | emergency lessons | ±10 kt, marked "the nearest FAA standard" | ACS PA.IX.B |
| threshold of "look outside at least every N s" | normal lessons | 5 s | Aeronautical Information Manual 8-1-6 c.3 |
| threshold of "no single look at the instruments longer than N s" | normal lessons | 5 s | AIM 8-1-6 c.3 ("no more than 4 to 5 seconds on the instrument panel for every 16 seconds outside") |

No suggestion is made where the FAA gives no number: RPM and vertical-speed targets, altitudes in an emergency, and scan-path rules for instrument lessons (the Instrument Flying Handbook describes the scan errors without numbers).

How it behaves:

- A suggested rule **stays set aside** — nothing suggested is used in a lesson until a person accepts it. Its reason reads *"Tolerance +10 / −5 kt is a suggestion from FAA Private Pilot ACS (FAA-S-ACS-6C), PA.IV.A …, not from this document. Check it, then add the rule back"*.
- The draft page shows a banner, **"N set-aside rules have an FAA suggestion pre-filled"**, with **Accept all N FAA suggestions** (adds them all back). Or open each one under **Set aside**, change the number if the school trains to another standard, and tick it.
- The rule carries a chip **FAA suggestion: ±100 ft** and a note line **"Not from this document — FAA suggestion: tolerance ±100 ft from …"**. The note is saved and exported with the rule, so it stays visible later where the number came from. Changing the number removes the chip: it is then the reviewer's own number.
- The circuit check runs with the suggested tolerance; a rule a correct circuit would break is set aside for that reason instead.
- A set-aside rule that still has no number — in a draft, on **SOP Rules**, or imported from an older export — shows the same suggestion with a **Fill in** button.

These are the **private-pilot checkride** standards. A school that trains to tighter ones uploads its own handbook (training handbook → "school's own"); wherever a document gives the number itself, nothing is suggested.

Set aside vs switched off: a rule that is **switched off** is a finished rule the school chose not to check for now; a **set-aside** rule is not (yet) part of the set at all, and may be unfinished.

### Lesson types

Every rule says which **kind of lesson** uses it (*Used in* in the rule editor). A drafted rule gets exactly one:

| Lesson type | For | Rules get it automatically when |
|---|---|---|
| **Normal circuit** (default) | ordinary lessons flown by outside reference | everything else |
| **Emergency** | emergency and abnormal-procedure lessons | it comes from the emergency part of the document (POH Section 3, a handbook chapter titled Emergency / Abnormal); or its title or quote says so ("engine failure", "forced landing", "no power is available", …); or, in a POH, the LLM tagged it `emergency`. A **go-around** is a normal procedure whatever the LLM tagged it, and in a handbook the tag alone is not enough |
| **Instrument flying** | lessons flown by reference to the instruments | it comes from the Instrument Flying Handbook, or from a chapter about instrument flying in another handbook — **every** such rule, instrument lessons only |

A lesson loads **only the rules of its own type**. That is what lets these rules be saved and *in use* without harming a normal circuit:

- an engine-failure glide speed (68 KIAS) would flag every normal climb if it were checked there; as an *Emergency* rule it is only checked in an emergency lesson;
- "eyes on the attitude indicator 80–90 % of the time" (instrument flying) and "about 90 % of attention outside" (visual flying) cannot both hold in one lesson.

**Instrument rules are instrument-lesson rules only.** Nothing drafted from the Instrument Flying Handbook is checked in a normal circuit. The label is on the rule everywhere it appears, so neither a person nor a model handed the rule can take it for a general one:

- the lists show a chip — **Instrument-flying lessons only**, **Emergency lessons only** — and have a *Used in* filter; the editor says "this rule is never checked in a normal circuit lesson";
- exports carry `lesson_types`, a `used_in` text, and start the plain-English `summary` with it: `[Instrument-flying lessons only] Spend at least 80% of the time looking at the attitude indicator…`;
- every rule a lesson loads (`sop/store.py active_rules`, `GET /sop/active/…`) carries `used_in` in words as well as `lesson_types` — and a normal lesson is never given the instrument or emergency rules in the first place.

A reviewer can still give one rule more than one lesson type in the editor (e.g. a school that wants "don't stare at one instrument for more than N s" checked in circuits too); that is a deliberate choice per rule, never the default.

The correct-circuit check only runs on rules used in normal lessons.

A lesson says which type it is in its YAML (`lessons/<lesson>.yaml`); without the line it is a normal lesson:

```yaml
rules_lesson_type: emergency     # normal (default) | emergency | instrument
```

`GET /sop/active/{aircraft}?lesson_type=emergency` returns the same selection.

### Aircraft-relative speeds

A handbook applies to every aircraft, so it gives speeds like "1.3 VSO" or "the recommended landing speed", not knots. An airspeed rule can therefore be **measured from one of the aircraft's own speeds** (*Measured from* in the editor):

| Field | Meaning |
|---|---|
| `ref_speed` | which speed: final approach speed, VSO, VS1, VR, VX, VY, VFE, VA, VNO, VNE |
| `ref_factor` | a multiplier (1.3 for "1.3 VSO"; normally 1) |
| `target` / `min_value` / `max_value` | then **differences** from factor × speed, in knots (0 = exactly that speed) |

Examples: *"within +10/−5 KIAS of the recommended landing speed"* → target 0, +10 / −5, measured from the final approach speed. *"not more than 1.3 VSO"* → maximum 0, measured from 1.3 × VSO.

The number comes from the aircraft profile (`profiles/aircraft/<id>.yaml`, `v_speeds`) **when a lesson starts**: in the Cessna 172SP the first example becomes 65 kt +10 / −5. The editor shows what the rule comes to in each aircraft. Profile keys: approach speed = `vref`, else `approach`, else `final`; the others are `vs0`, `vs1`, `vr`, `vx`, `vy`, `vfe`, `va`, `vno`, `vne`.

**All of an aircraft's speeds live together in its profile**, and the Cessna 172SP profile gives every one of them (VX 62, VS1 48, VFE 85, VA 105, VNO 129 were added; `vfe` is the flaps 10–30° limit, `vfe_10: 110` the flaps-10 one), so no rule is skipped in it. A new aircraft profile should give them all too. If one is missing, a rule measured from it cannot be computed for that aircraft, and MAVIS says so in three places instead of skipping quietly: a warning on the upload page naming the profile and the speeds, a warning on the draft that uses such a rule, and at lesson start `state["sop"]["unresolved"]` (rule title and speed) plus a log line. `python -m sop.selftest` fails for a profile with a missing speed.

The correct-circuit check uses each aircraft's own number, so "not more than 1.3 VSO on final" (52 kt in the C172, whose approach speed is 65) is set aside with that explanation.

### Tags

Every rule has 1–4 tags saying what it is about. The LLM proposes them, and the tag implied by the rule's measurement is always added, so filters are reliable. You can change them in the rule editor.

| Tag | Means |
|---|---|
| Flight telemetry | how the aircraft is flying: airspeed, altitude, climb/descent rate, attitude, heading |
| Pilot controls | what the pilot sets or moves: throttle, flaps, yoke, rudder, brakes, trim |
| Engine | RPM, power setting, mixture, engine start / run-up |
| Aircraft limit | hard limitations: never-exceed, max flap speed, max RPM |
| Procedure | a checklist item or normal-procedure step |
| Configuration | how the aircraft must be set up for a phase (flaps set, lights, mixture) |
| Communication | radio calls, ATC, transponder |
| Lookout & awareness | lookout, traffic, terrain, orientation, weather decisions |
| Scan path | where the pilot looks: instrument cross-check, fixation / omission / emphasis, time outside vs inside (always on gaze rules) |
| Emergency | emergency or abnormal situations |

The vocabulary lives in `sop/schema.py` (`TAGS`). Adding a tag there adds it to the prompt, the validator and the pages.

### The LLM prompt

The exact prompt is `system_prompt()` in `sop/llm_parse.py` (`PROMPT_VERSION`). Every saved rule set records the prompt version and the pages it was drafted from.

**v5** (current) adds three lines after a full v4 run: the `emergency` tag is only for emergency / abnormal procedures (a go-around is not one); a range stays a range ("65 to 75 KIAS" is not a target of 75); and the takeoff and climb speeds must not get lost among the emergency rules — the v4 run produced 37 emergency rules but no rotation or climb speed (the page now also warns when that happens).

**v4**: emergency procedures are extracted again, because emergencies get their own lessons. From each emergency checklist the model drafts one "not observable" rule plus each airspeed the checklist gives as a target, all with `emergency` as the first tag; the pipeline gives them the lesson type *Emergency*. (v3 returned nothing for emergencies; v2 turned them into limits on normal flight.)

A v3 run of the same POH gave 21 rules instead of 61: the emergency checklists, the reversed RPM rule and the cruise-power rule were gone, and the 10–30° flap limit was found. Still wrong, and now caught by the validator rather than left to the prompt: the same flap limit drafted twice, the flap-retraction and short-field speeds as maximum speeds, and "Normal climb 75–85 KIAS" wrongly set aside by the circuit check.

**v3** changed the following from v2, after reviewing a second full Cessna 172S run (61 rules, pages 2–136):

- **Emergency and abnormal procedures produce nothing** (changed again in v4). v2 allowed one "not observable" rule per emergency checklist; a full run gave 22 of them plus emergency speeds as limits for normal flight.
- A section **"What a number means"**: "or LESS" is a maximum and "or MORE" a minimum; one speed to fly is a band, never a limit with min = max or with the number left in `value`; the speed to reach *before* retracting flaps is not a maximum speed; manoeuvring speed ("abrupt use of the controls is prohibited above…") limits control movements, not airspeed; in a KCAS/KIAS table use the KIAS column; never `== 10` on flap angle (the simulator reports 9.9°), use `>` / `>=`.
- **Skipped:** engine-start, taxi and ground-run settings, cruise settings, short-field and soft-field technique, demonstrated crosswind.
- **Go-around** numbers don't become limits (the simulator can't tell a go-around from a normal climb): one "not observable" rule.
- "Not observable" is only for something the pilot must **do**; facts and advice are not rules.
- Every field is written out (the JSON schema now requires all of them, `null` when unused), so the model can no longer drop the field that carries the number.

**v2** changed the following from v1, after reviewing a full Cessna 172S POH run where most drafted rules were wrong:

- It states what the rules are for: normal VFR circuit lessons in a simulator, where a wrong rule is worse than a missing one.
- It says what to extract: limitations, normal-procedure targets, one rule per normal checklist.
- It says what to skip: equipment supplements, performance tables, maintenance, spins/ditching, quantities the sim can't measure, ground-test settings.
- Emergency, autopilot and other conditional numbers must never become limits for normal flight.
- It adds a self-check before each rule: "would a correct circuit break this?".
- It prefers limits for SOP ranges.
- It asks for tags.
- It gives worked right/wrong examples taken from that run.

## Training handbooks (FAA or the school's own)

A POH says what the **aircraft** allows. How to **fly the circuit** (pattern height, turns, stabilized approach) and **where to look** (scan path) come from training handbooks. Those apply to every aircraft, so their rules are stored separately and added to every lesson.

**Which books.** For now the FAA handbooks are the baseline; a school's own handbook can be uploaded the same way (*Flight school handbook / SOP*).

| Book | Chapters worth reading (suggested automatically) | What it gives |
|---|---|---|
| Airplane Flying Handbook (FAA-H-8083-3C) | Basic Flight Maneuvers, Takeoffs and Departure Climbs, Airport Traffic Patterns, Approaches and Landings | Pattern height, turns, stabilized approach, go-around, outside vs instrument attention |
| Instrument Flying Handbook (FAA-H-8083-15B) | Airplane Attitude Instrument Flying (and Airplane Basic Flight Maneuvers) | The instrument scan and its errors: fixation, omission, emphasis |
| Pilot's Handbook of Aeronautical Knowledge (FAA-H-8083-25C) | Airport Operations, Aeromedical Factors | Traffic-pattern procedure; the visual scan for collision avoidance |

The Instrument **Procedures** Handbook (FAA-H-8083-16B) and the Aviation Weather Handbook are recognised and flagged: they hold no circuit rules.

**Upload.** Choose **Training handbook** and the book (or *Detect from the PDF*). Upload one book at a time; a full book or a single-chapter PDF both work. PDFs up to 500 MB are accepted (`sop.max_pdf_mb`); the file is streamed to disk, never held in memory.

**Recognising the book.** A PDF is only identified by its **own** name — never by a mention in its text, because the handbooks cite each other on almost every page (the Instrument Flying Handbook keeps referring to the PHAK). In order: the PDF's metadata title; a cover / title page (one of the first three pages, if it has few words); a running header or footer that is mostly the book's name and appears on at least 40 % of the pages. If none of these is found:

- a book **you chose** is used as chosen, with no warning;
- with *Detect from the PDF*, the page picker asks which book it is before any LLM time is spent.

A "this PDF's own title says it is …" warning therefore only appears when the PDF really names a different book.

- Two-column pages are read column by column, so sentences aren't interleaved across columns.
- The page picker lists **chapters** (from "Chapter 8 …" headings and page labels like `8-3`) and pre-selects the ones above. Everything else is theory and only costs LLM time: a handbook chapter is ~10–40 pages, and each page is roughly one LLM call.
- If a handbook is uploaded as a POH (or the wrong book is chosen), the picker says so; the document type and book can be changed there before any LLM time is spent.

**How much the LLM reads per call.** A handbook explains a requirement over paragraphs, so handbook runs differ from POH runs:

| | POH | Handbook |
|---|---|---|
| Context window | `sop.poh_num_ctx` (12288; prompt v3 is longer and every rule writes all its fields) | `sop.handbook_num_ctx` (16384) |
| Text per call | `sop.chunk_chars` (6000, about one page) | `sop.handbook_chunk_chars` (9000, about two pages) |
| Overlap between calls | none | `sop.handbook_overlap_chars` (1200): each section starts with the end of the previous one, marked as context only |
| Answer limit | 3072 tokens | 4096 tokens |

None of these keys needs to be in `config.yaml`; add one only to change it. The larger window costs roughly 1.5 GB more VRAM while a handbook is being read. If an answer is cut off at the limit, the rules it had finished writing are kept.

**The handbook prompt (H4)** is `handbook_prompt()` in `sop/llm_parse.py` (`HANDBOOK_PROMPT_VERSION`). Compared with the POH prompt it:

- says most of a textbook contains **no** rule, and an empty result is normal;
- has a focus paragraph per book (what to look for in the AFH, IFH, PHAK, a school handbook);
- skips **anything aircraft-specific** (V-speeds, "1.3 VSO", flap and power settings — those come from the POH), maneuvers that aren't flown in a circuit (steep turns, stalls, ground reference, emergencies…), and IFR procedures;
- adds the **gaze** rule type (below), and tells the model to leave the number empty when the book describes the behaviour without one ("avoid fixation", "check the airspeed frequently") — the instructor sets it;
- (H2) tells the model to **read the whole passage, not one sentence**: the sentences around a requirement say which instrument, which phase and any number; one passage makes one rule;
- (H2) asks for the **whole supporting passage** as the quote (usually 2–4 sentences, up to about 700 characters; it may run over a page break) instead of a single line;
- (H2) asks for a **note**: one or two sentences on what the surrounding text says that helps set or check the rule. It is shown above the rule as *What the book says here* and stored in the rule's note, labelled as an LLM summary — it is not verified, unlike the quote.

**H5** (current): the `emergency` tag is only for emergencies (H4 tagged "go around if the approach is not stabilized" as one); a share must be printed in the passage as a percentage of time or attention. H4 runs gave the 80–90 % attitude-indicator rule, the three scan errors and primary-instrument rules from the Instrument Flying Handbook, and 1.3 / 1.4 × VSO rules from the Airplane Flying Handbook; the mistakes left (an invented "10 %" from "1/10 the span", example titles on unrelated passages, a 200-knot limit, pattern altitude on base and final) are handled by the validator.

**H4**:

- **Aircraft-relative speeds are extracted, not skipped**: the model sets `ref_speed` / `ref_factor` and gives the numbers as differences (see [Aircraft-relative speeds](#aircraft-relative-speeds)). Flap and power settings are still skipped.
- **Emergency procedures are kept**, tagged `emergency`.
- The **Instrument Flying Handbook** focus asks for what an eye tracker can check: the scan technique where the book puts a number on it ("eyes remain on the attitude indicator 80–90 percent of the time"), the three scan errors (one rule each), and one *look at it at least every N s* rule per primary instrument of each basic manoeuvre (level flight, climb, turn, descent). Still not rules: supporting instruments, and sentences that only say what an instrument shows.

H3 runs gave 18 rules from the Airplane Flying Handbook (54 before) and 10 from the Instrument Flying Handbook (77 before), with the 90 % / 10 % attention rules correct. What H3 still got wrong — a rule from a "common errors" list, the descent rate drafted as both a limit and a "band", every scan rule without a number written as a share of time, the approach-speed rule left empty — is handled by the validator now.

**H3** followed a review of full Airplane Flying Handbook (54 rules) and Instrument Flying Handbook (77 rules) runs:

- A scan-path rule **always names where to look and what is checked**. 57 of the 77 scan rules in the IFH run and 16 of 19 in the AFH run had neither, because the schema let the model leave fields out; it no longer does.
- **A number in the quoted passage must be in the rule.** "Approximately 90 percent of the pilot's attention … outside" came back with no number; so did "usually 1,000 feet" and "generally 500-1000 fpm". Percent shares are copied as printed (`90`, unit `percent`).
- **Never invent a number.** Twelve IFH rules carried a made-up "50". (The validator now also removes a scan number that isn't in the quote.)
- A list **"Not a rule"**: what an instrument shows or is "primary / supporting" for; how to move the controls (rudder, aileron, brakes, pitch); "Common Errors" lists; what to do after a fault (ballooning, bouncing, porpoising, floating, wheelbarrowing); the same requirement stated again.
- "Not observable" rules are **rare**: only an action required on every normal circuit.
- Aircraft-relative values ("+10/−5 KIAS of the recommended landing speed", "1.3 VSO") are skipped rather than drafted with the number missing (changed in H4).
- The **Instrument Flying Handbook** focus is narrower: the three scan errors (one rule each) and passages that say how often or how long to look at a named instrument. Shares of time for flying *solely* by instruments were skipped (changed in H4, where lesson types keep them apart from the visual-circuit rules).
- The prompt's own example sentences must not be quoted.

**Gaze (scan-path) rules.** `kind = 'gaze'`: where the pilot looks, measured by the eye tracker.

| Field | Values |
|---|---|
| Look at (`aoi`) | an AOI **role**: outside view, any instrument (panel), attitude indicator, airspeed indicator, altimeter, vertical speed indicator, heading indicator, turn coordinator, engine instruments |
| Check (`gaze_measure`) | `max_gap` look at it at least every N s (omission) · `max_dwell` no single look longer than N s (fixation) · `min_share` / `max_share` at least / at most N % of the time (emphasis, time outside) |
| Threshold (`value`) | seconds, or a share stored as 0–1 (shown as %) |

A gaze rule without a threshold is drafted and **set aside automatically** ("the book gives no number for this"). For "look outside at least every N s" and "no single look at the instruments longer than N s" in normal lessons, the AIM's 5 s is pre-filled as an [FAA suggestion](#faa-suggestions-for-missing-numbers). Check or set the threshold and add the rule back — in the draft, or later on the SOP Rules page. Each gaze rule also has a [lesson type](#lesson-types): rules from the Instrument Flying Handbook are for instrument-flying lessons, the dwell / gap ones for normal lessons too. Rules point at AOI *roles*, not at zone names, because AOI zones are drawn and named freely in IRIS: each zone will need a role picked from this list (next step) so the rules know which zone is the airspeed indicator.

**Checks.** Handbook rules go through the same validator (quote must be in the text, units, plausibility). The correct-circuit check runs against **every** aircraft profile, so a rule that only fits one aircraft (e.g. a climb speed) is set aside with the aircraft named.

**Save.** The rule set gets no aircraft; it's saved for the book (`handbook = 'afh'`, …) and made active. **One active set per book**: saving a new AFH set deactivates the previous AFH set and leaves the IFH and PHAK sets alone.

## SOP Rules (`/sop/rules`)

- **Aircraft tabs** show each aircraft profile and its active rule set. The **Training handbooks** tab shows the handbook rule sets (one active per book), which apply to every aircraft.
- **Rule set bar:** switch between the aircraft's sets (older ones are kept, inactive), **Rename**, **Make active** / **Deactivate**, and **Delete** (inactive sets only).
- **Search and tag filter** narrow the list.
- **Filters:** *All* / *On* / *Switched off* are the rules of the set; *Set aside* lists the [set-aside rules](#set-aside-rules) kept with it.
- **Export** (CSV or JSON) includes every rule with its status and, for set-aside rules, the reason.
- **Rules:** edit in place (including tags), then **Save changes** or **Revert**. Switching away from unsaved edits asks first. The **On/Off** switch disables a rule without deleting it. You can also **+ Add rule** and **Delete rule**.
- An edit that would break a rule is refused with the reason, for example removing a band's tolerance.
- **History of this rule** / **All changes** show who changed what and when, with before → after values for edits.

## Which rules a flight uses

When a lesson starts, MAVIS picks the aircraft actually loaded in the simulator (`sop/runtime.py`):

1. MSFS reports `TITLE`, `ATC MODEL` and `ATC TYPE`.
2. These are matched against each profile's `sim_match` list (case-insensitive substring; the longest match wins).
3. MAVIS loads the **enabled rules of that aircraft's active rule set**, plus the enabled rules of **every active training-handbook set**, into `state["sop"]`. Each rule carries its `layer` (`aircraft` / `general`) and, for handbook rules, its `handbook`.
4. Only the rules of the lesson's **type** are kept (normal unless the lesson YAML says `rules_lesson_type: emergency` or `instrument` — see [Lesson types](#lesson-types)), and rules **measured from an aircraft speed** get that aircraft's number ([Aircraft-relative speeds](#aircraft-relative-speeds)). Each loaded rule carries `lesson_types` and `used_in` ("Instrument-flying lessons only"). `state["sop"]` reports `lesson_type`, `n_unresolved` and `unresolved` (the rules left out because the profile lacks their speed — empty for the Cessna 172SP).

The React in-flight view shows a chip next to the phase, e.g. **SOP · C172 SOP rev3 + 12 handbook rules**.

```yaml
# profiles/aircraft/c172sp_g1000.yaml
sim_match: ["C172", "Cessna 172", "Skyhawk", "172SP"]
```

| Situation | Result |
|---|---|
| `sop.aircraft_override` is set in `config.yaml` | That profile's rules are used |
| The simulator reports nothing (mock, replay) | Config's `aircraft_profile` is used |
| The simulator reports an aircraft that matches **no** profile | **No aircraft rules are applied**; C172 rules are never used on another type. Handbook rules still apply |
| The aircraft has no active rule set | The lesson runs with the handbook rules only (or none) |

Loading never delays the start of a lesson.

To add an aircraft type: copy a profile YAML, set its name, speeds, flaps and `sim_match`, restart MAVIS, then upload its SOP.

## Database (`db/schema_sop_rules.sql`)

| Table | Holds |
|---|---|
| `sop_documents` | One row per saved PDF (SHA-256, OCR'd pages, local path) |
| `sop_pages` | Extracted text per page. It feeds the page view and the quote checks for later edits |
| `flight_rule_sets` | A named set of rules. `scope = 'aircraft'`: for one `aircraft_profile`, **at most one `active` per aircraft**. `scope = 'general'`: from a training handbook (`handbook` = `afh`, `ifh`, `phak`, `school`, `other`), applies to every aircraft, **at most one `active` per book**. Also records `prompt_version` and `page_selection` (the pages the LLM read) |
| `flight_rules` | One rule. See the rule types below. A `CHECK` constraint enforces each type's required values for rules in use; a rule with `set_aside = true` (kept but not used, with `set_aside_reason`) may be incomplete. Also stores `tags`, `enabled`, provenance (`source_quote`, `source_page`, `quote_score`) and `llm_draft` (the LLM's original proposal, before any human edit) |
| `flight_rule_history` | Every create / update / delete / activate / deactivate / rename, with `changed_by`, `changed_at`, and full `before` / `after` JSON snapshots |

The rule types:

| `kind` | Values used | Meaning |
|---|---|---|
| `band` | `target`, `tol_hi`, `tol_lo` | Hold a target ± tolerance |
| `limit` | `min_value` / `max_value` | Stay within limits |
| `event` | `op`, `value`, `gate_phase` | Must be true by the start of a phase |
| `capture` | `capture_at`, `min_value` / `max_value` | Checked at lift-off, touchdown, … |
| `gaze` | `aoi`, `gaze_measure`, `value` | Where the pilot looks (eye tracker): see [gaze rules](#training-handbooks-faa-or-the-schools-own) |
| `unobservable` | none | The instructor assesses it (radio calls, checklists) |

Any rule can also have:

- `lesson_types`: the kinds of lesson that use it — `normal`, `emergency`, `instrument` (default `{normal}`);
- `ref_speed`, `ref_factor`: an aircraft speed an airspeed rule is measured from; its numbers are then differences from it;
- `phases`: the lesson phases it applies in (empty means every phase);
- `conditions`: `[{metric, op, value}]`, all of which must hold for the rule to apply.

All numbers are stored in MAVIS `Telemetry` units: kt, ft, ft/min, deg, rpm, and throttle 0–1.

Apply the schema once. It is idempotent and safe to re-run:

```
psql -U postgres -d postgres -f db/schema_sop_rules.sql
```

Run it again after updating MAVIS: each version only adds what is missing. **v5** adds `lesson_types`, `ref_speed` and `ref_factor` to `flight_rules` (existing rules become normal-circuit rules). If it hasn't been run, the *Rules database* chip on the upload page turns red and says so, and Save is refused until it has.

The v3 part (training handbooks, gaze rules) is additive: existing rule sets become `scope = 'aircraft'` and keep working.

It also drops the three tables from this branch's earlier iteration (`sop_rulesets`, `sop_rules`, `sop_aircraft_bindings`), which only ever held test data.

MAVIS writes only these tables. This is a deliberate, scoped exception to INTEGRATION_PLAN.md's Option A, where IRIS is the only writer; IRIS only reads these tables.

## Local LLM: what to install

MAVIS talks to **Ollama** on the same laptop. No new model is needed: SOP parsing uses the model MAVIS already runs for coaching, **`qwen2.5:14b-instruct`**.

- It's already loaded and pinned in VRAM, so parsing adds no memory and no model swap.
- It supports Ollama's JSON-schema structured output well enough for this job: every draft is checked and reviewed anyway.

On the sim laptop (Windows):

1. **Ollama.** Install from <https://ollama.com/download> (Windows installer). Keep it reasonably current: **0.5 or newer** is required for schema-constrained output. Older versions still work, but fall back to plain JSON mode.
2. **The model.** Run `ollama pull qwen2.5:14b-instruct` (about 9 GB). Check it with `ollama list`.
3. **Tesseract OCR** (only needed for scanned SOPs). Install the UB Mannheim Windows build. Either add it to PATH or set `sop.tesseract_cmd: C:/Program Files/Tesseract-OCR/tesseract.exe`.
4. **Python dependencies** (MAVIS venv): `.venv\Scripts\pip install -r requirements.txt`, which adds `pymupdf`, `pytesseract`, `pillow` and `asyncpg`.

### Switching the model

A stronger model can draft the rules without changing the model the coach talks with. Three ways, from most to least convenient:

- **Upload page → "LLM that drafts the rules".** One button per installed model. Click one: every run from then on uses it, including *Run LLM again*. The choice is kept in `data/sop/settings.json` on the MAVIS machine (not in git, not in `config.yaml`), so it survives a restart. Clicking the configured model goes back to `config.yaml`'s. Switching is refused while a document is being processed.
- **Per run:** the **LLM model** list in the page picker (or *Change pages or model* on a draft). For one comparison run; the default stays as it is.
- **`sop.model` in `config.yaml`:** the model used when nothing was chosen on the page.

To try another model, install it on the sim laptop and press ↻ on the card:

```
ollama pull qwen3.6:27b
```

A model that isn't installed can't be chosen; the API answers with the `ollama pull` command.

**GPU memory.** The laptop RTX 5090 has 24 GB, which holds **one** large model. When rules are drafted with a model other than the coach's:

1. before the run, every other model is unloaded from Ollama (the coach's model leaves the GPU);
2. the drafting model loads with the first section (that call can take a minute; the timeout is raised to at least 600 s);
3. after the first section MAVIS asks Ollama how much of the model is on the GPU (`ollama ps` shows the same). Below 98 % the draft gets a warning: *"… does not fit on the GPU (62% of it is on the GPU, the rest runs on the CPU), so drafting is slow"*. Then close other GPU programs, lower `sop.handbook_num_ctx` / `sop.poh_num_ctx`, or use a smaller model;
4. after the run — and whenever the run pauses because a lesson started — the drafting model is unloaded again. The coach's model reloads by itself on its next line, so the first coaching line after a parse can be a template phrase.

Nothing is unloaded when the drafting model **is** the coach's model.

**Timing.** Each draft records how long the LLM took (`llm_seconds`): shown in the draft's status line ("big-model · prompt vH5 · drafted in 6 min 12 s") and written to draft exports, next to `llm_model` and `prompt_version`. Comparing models: run the same pages with each, export both drafts, compare the rules and the time.

Candidates that fit a 24 GB card (sizes from the Ollama library, Q4): `mistral-small3.2:24b` (about 15 GB; leaves room for the 16k context) and `qwen3.6:27b` (about 18–19 GB; tight with the 16k handbook context — watch for the GPU warning). The 14B model's typical mistakes on long prompts — leaving fields out, copying the prompt's examples, inventing a number — are what a larger model is expected to reduce; the validator catches them either way.

- **Thinking mode:** keep `sop.think: false` (the default). Reasoning models (qwen3.x is one) must have thinking turned off for JSON output.
- **Test first:** parse one known SOP and compare the rules before relying on it.

## Configuration (`config.yaml`)

```yaml
database:
  url: postgresql://postgres:postgres@localhost:5432/postgres   # DATABASE_URL overrides
sop:
  model: ""            # empty = coach.model. The model switch on the Upload page overrides this (data/sop/settings.json)
  ollama_url: ""       # empty = coach.ollama_url
  think: false         # thinking OFF for reasoning models; "omit" = don't send the flag
  llm_timeout_s: 300
  num_ctx: 8192
  chunk_chars: 6000
  # poh_num_ctx: 12288           # context window for a POH run (the larger of this and num_ctx)
  # optional -- these are the built-in defaults, add a line only to change it:
  # max_pdf_mb: 500              # upload limit
  # handbook_num_ctx: 16384      # context window for training handbooks
  # handbook_chunk_chars: 9000   # handbook text per LLM call (~2 pages)
  # handbook_overlap_chars: 1200 # repeated from the previous section for context
  pdf_dir: data/sop    # PDFs + drafts/ (git-ignored)
  tesseract_cmd: ""
  ocr_lang: eng
  aircraft_override: ""
```

## Behaviour when something is down

- **Postgres down:** MAVIS still flies. Uploading and reviewing still work, because drafts are local files; Save and the SOP Rules page answer 503 until Postgres is back.
- **Ollama down:** the PDF is still read. You can add rules by hand, or press **Run LLM again** once Ollama is up.
- **MAVIS restarted mid-processing, or processing failed:** the draft is marked failed. **Run again** re-drafts from the pages already read, or, if reading the PDF never finished, re-reads the PDF kept in `data/sop/` first.
- **Windows "Access is denied" (WinError 5) on `data/sop/drafts/...`:** another program (usually Defender, or OneDrive syncing `Downloads`) briefly held the draft file open. MAVIS now serialises its own draft-file access and retries these writes. If it still happens, press **Run again**. If it keeps happening, move the IRIS folder out of `Downloads`/OneDrive, or add `MAVIS\ai-instructor\data\sop` to Defender's exclusions.
- **Missing Python dependencies:** the `/sop` API isn't mounted, and MAVIS runs as before.

## Tests

`python -m sop.selftest` covers lesson types (instrument-handbook rules are instrument-only; a go-around is not an emergency), that every aircraft profile gives every reference speed, phase trimming and the provisional-tolerance circuit check, coverage warnings, aircraft-relative speeds (resolving them per aircraft, the circuit check with each aircraft's number), the automatic set-aside checks (reversed direction, min = max, invented scan numbers, table quotes, emergency pages), reading an exported rules file back in, set-aside reasons and storing malformed set-aside rules, the validator, quote matching, units, dedupe, aircraft cross-checks, chunking and aircraft → profile matching, plus gaze rules, handbook chapters, book detection and the handbook prompt, the FAA suggestions (which value for which rule, none where the FAA gives no number, the note line, a suggestion a correct circuit would break or that repeats a rule in use), quotes shortened with "..." (found piece by piece; invented pieces still fail; dot leaders are not an ellipsis), finding a quote in a 400-page document from the right page, a wrong page or no page (and doing so quickly), reversed scan rules, the always-true flap condition, and duplicates that are not merged across lesson types. It needs no Ollama, Postgres or PDF.

Live rule checking during a flight is not built yet: rules (including gaze rules) are stored, reviewed and loaded at lesson start, ready for the evaluator.
