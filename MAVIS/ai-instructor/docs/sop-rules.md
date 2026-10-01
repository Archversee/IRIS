# SOP → flight rules

An instructor uploads an aircraft's Standard Operating Procedure (PDF). MAVIS reads it and a **local** LLM drafts machine-checkable flight rules. The instructor reviews and edits them, then saves them to the database as that aircraft's active rule set. Saved rules can be edited at any time, and every change is logged.

The same pages also take **training handbooks** — the FAA handbooks, or a flight school's own. Their rules (circuit procedure, scan path) apply to **every aircraft** and are used together with each aircraft's own rules. See [Training handbooks](#training-handbooks-faa-or-the-schools-own).

Everything runs on the sim laptop: PyMuPDF and Tesseract read the PDF, and Ollama drafts the rules. Nothing leaves the machine.

## Where it lives

The pages are in the IRIS React app (the dashboard at `http://localhost:3000`), under **MAVIS → Curriculum → Preflight & Cockpit**:

| Page | What it's for |
|---|---|
| **Upload SOP or handbook** — `/sop/new` | Choose *Aircraft POH / SOP* or *Training handbook* → upload → OCR / LLM → review and edit the drafted rules → **Save** |
| **SOP Rules** — `/sop/rules` | Pick an aircraft (or **Training handbooks**), then browse, edit, add, delete, or switch off its saved rules; see the change history; switch between rule sets |

The pages call MAVIS's `/sop/*` API through the `/mavis` proxy prefix. There is no PIN on these pages. The **Editing as** box in the page header records who made each change; it is remembered per browser.

## Upload SOP (`/sop/new`)

1. **Upload.** Drop the PDF. Status chips show whether the local LLM, OCR and the database are available, and whether a lesson is in progress (uploading waits until it ends).
2. **Choose pages** (documents over 40 pages). MAVIS reads the text of every page, without OCR, which is fast. It then detects the POH sections from the running headers and footers ("SECTION 2 / LIMITATIONS", page labels like `2-4`).
   - It suggests **Section 2 Limitations** and **Section 4 Normal Procedures**. Those hold the rules the simulator can check. The optional-equipment supplements (autopilot, GPS, transponder…) only produce noise.
   - Tick sections, or type ranges (`13-20, 34-63`), preview a page, and choose the **aircraft**. The aircraft is given to the LLM and used for the circuit check below.
   - Documents of 40 pages or fewer skip this step and are read whole.
3. **Processing.** A progress bar steps through: OCR the chosen scanned pages → draft rules → check rules. You can leave the page; processing continues on the MAVIS machine. The draft is listed under **Unsaved drafts** when you come back.
4. **Review and edit.** Nothing is in the database yet. The draft is a local file in `MAVIS/ai-instructor/data/sop/drafts/`, and every edit is autosaved to it. A refresh, a closed tab or a MAVIS restart loses nothing.
   - The left side lists the rules by SOP page, each with a plain-English summary (*"Hold indicated airspeed at 74 kt +10 / −5 kt during Climb out."*) and its **tags**. Filters: **Needs fixing**, **Warnings**, **Set aside**, plus a tag filter.
   - The right side has the rule editor and the SOP page the rule came from. You can show the page image, with the quote highlighted, or the extracted text.
   - Untick a rule to **set it aside**, so it isn't saved. **+ Add rule** adds your own. **Run LLM again** re-drafts from the same pages; **Change pages** re-drafts from different ones. Both keep the rules you added by hand.
   - Keyboard: `J`/`K` move between rules, `X` includes or sets aside the current rule.
5. **Save.** Choose the aircraft and a rule set name, then press **Save N rules**. One transaction:
   - writes the SOP document and pages, the rule set, its rules, and a `create` history entry per rule;
   - makes the set **active** for that aircraft (the previously active set is kept, inactive);
   - deletes the draft file.

   **Save** stays disabled until an aircraft is chosen and nothing included needs fixing.

### What "needs fixing" means

The LLM only *drafts*. Every rule is checked deterministically (`sop/validate.py`) when it's drafted, on every autosave, and again at Save.

A rule **needs fixing** (it can't be saved while it's included) if:

- its **source quote can't be found** in the SOP text (below 85 % match). These are set aside automatically; this is how made-up rules are caught;
- its unit doesn't fit what it measures (e.g. feet for airspeed), or a value is physically implausible;
- its type is missing a value: a band without a target or tolerance, a limit without a min or max, and so on.

A rule also **needs fixing** if its numbers appear in the quote only as a quantity the simulator can't measure, e.g. "Oil Pressure, Minimum: 20 PSI" turned into "RPM ≥ 20". The units recognised are PSI, °F/°C, gallons/GPH, lbs, in.Hg, amps/volts, MHz, hours and NM.

A rule is **set aside automatically**, with the reason shown, if **a correctly flown circuit would break it** (`sop/profile.py`). MAVIS builds the expected circuit from the lesson file and the aircraft profile:

- speeds per phase (rotate at Vr, climb at Vy, downwind / base / final speeds);
- pattern height;
- flap settings per phase;
- typical engine RPM / throttle / vertical-speed / bank ranges, overridable per aircraft under `nominal:` in its profile YAML.

This catches rules meant for another situation, e.g. the engine-out glide speed as a climb limit, the 1800 RPM magneto check as an RPM limit, or autopilot-only speeds. You can re-include a set-aside rule: it then shows as a warning, not a block. The same check runs on edits to saved rules and is shown as a warning after saving.

A **warning** doesn't block saving. Warnings are shown for:

- a number that doesn't appear in the quoted text;
- a quote that isn't word for word;
- a rule from an OCR'd page (compare it with the page image);
- a corrected page number;
- dropped phases.

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

**v2** (current) changed the following from v1, after reviewing a full Cessna 172S POH run where most drafted rules were wrong:

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

**Upload.** Choose **Training handbook** and the book (or *Detect from the PDF*: the document number, e.g. `FAA-H-8083-3C`, or the title is recognised). Upload one book at a time; a full book or a single-chapter PDF both work.

- Two-column pages are read column by column, so sentences aren't interleaved across columns.
- The page picker lists **chapters** (from "Chapter 8 …" headings and page labels like `8-3`) and pre-selects the ones above. Everything else is theory and only costs LLM time: a handbook chapter is ~10–40 pages, and each page is roughly one LLM call.
- If a handbook is uploaded as a POH (or the wrong book is chosen), the picker says so; the document type and book can be changed there before any LLM time is spent.

**The handbook prompt (H1)** is `handbook_prompt()` in `sop/llm_parse.py` (`HANDBOOK_PROMPT_VERSION`). Compared with the POH prompt it:

- says most of a textbook contains **no** rule, and an empty result is normal;
- has a focus paragraph per book (what to look for in the AFH, IFH, PHAK, a school handbook);
- skips **anything aircraft-specific** (V-speeds, "1.3 VSO", flap and power settings — those come from the POH), maneuvers that aren't flown in a circuit (steep turns, stalls, ground reference, emergencies…), and IFR procedures;
- adds the **gaze** rule type (below), and tells the model to leave the number empty when the book describes the behaviour without one ("avoid fixation", "check the airspeed frequently") — the instructor sets it.

**Gaze (scan-path) rules.** `kind = 'gaze'`: where the pilot looks, measured by the eye tracker.

| Field | Values |
|---|---|
| Look at (`aoi`) | an AOI **role**: outside view, any instrument (panel), attitude indicator, airspeed indicator, altimeter, vertical speed indicator, heading indicator, turn coordinator, engine instruments |
| Check (`gaze_measure`) | `max_gap` look at it at least every N s (omission) · `max_dwell` no single look longer than N s (fixation) · `min_share` / `max_share` at least / at most N % of the time (emphasis, time outside) |
| Threshold (`value`) | seconds, or a share stored as 0–1 (shown as %) |

A gaze rule without a threshold can be drafted but **can't be saved** until the instructor sets one. Rules point at AOI *roles*, not at zone names, because AOI zones are drawn and named freely in IRIS: each zone will need a role picked from this list (next step) so the rules know which zone is the airspeed indicator.

**Checks.** Handbook rules go through the same validator (quote must be in the text, units, plausibility). The correct-circuit check runs against **every** aircraft profile, so a rule that only fits one aircraft (e.g. a climb speed) is set aside with the aircraft named.

**Save.** The rule set gets no aircraft; it's saved for the book (`handbook = 'afh'`, …) and made active. **One active set per book**: saving a new AFH set deactivates the previous AFH set and leaves the IFH and PHAK sets alone.

## SOP Rules (`/sop/rules`)

- **Aircraft tabs** show each aircraft profile and its active rule set. The **Training handbooks** tab shows the handbook rule sets (one active per book), which apply to every aircraft.
- **Rule set bar:** switch between the aircraft's sets (older ones are kept, inactive), **Rename**, **Make active** / **Deactivate**, and **Delete** (inactive sets only).
- **Search and tag filter** narrow the list.
- **Rules:** edit in place (including tags), then **Save changes** or **Revert**. Switching away from unsaved edits asks first. The **On/Off** switch disables a rule without deleting it. You can also **+ Add rule** and **Delete rule**.
- An edit that would break a rule is refused with the reason, for example removing a band's tolerance.
- **History of this rule** / **All changes** show who changed what and when, with before → after values for edits.

## Which rules a flight uses

When a lesson starts, MAVIS picks the aircraft actually loaded in the simulator (`sop/runtime.py`):

1. MSFS reports `TITLE`, `ATC MODEL` and `ATC TYPE`.
2. These are matched against each profile's `sim_match` list (case-insensitive substring; the longest match wins).
3. MAVIS loads the **enabled rules of that aircraft's active rule set**, plus the enabled rules of **every active training-handbook set**, into `state["sop"]`. Each rule carries its `layer` (`aircraft` / `general`) and, for handbook rules, its `handbook`.

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
| `flight_rules` | One rule. See the rule types below. A `CHECK` constraint enforces each type's required values. Also stores `tags`, `enabled`, provenance (`source_quote`, `source_page`, `quote_score`) and `llm_draft` (the LLM's original proposal, before any human edit) |
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

- `phases`: the lesson phases it applies in (empty means every phase);
- `conditions`: `[{metric, op, value}]`, all of which must hold for the rule to apply.

All numbers are stored in MAVIS `Telemetry` units: kt, ft, ft/min, deg, rpm, and throttle 0–1.

Apply the schema once. It is idempotent and safe to re-run:

```
psql -U postgres -d postgres -f db/schema_sop_rules.sql
```

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

### Optional: a bigger model for parsing only

If extraction quality isn't good enough on your SOPs, set `sop.model` to a stronger instruct model without changing the coaching model. Candidates are the Qwen 3.x ~27–35B class (about 17–22 GB at Q4).

- **VRAM:** the laptop RTX 5090 has 24 GB, so a model that size can't sit in VRAM next to the coaching model, Kokoro and Whisper. Ollama will swap models, which means the first coaching lines after a parse fall back to template phrases while the coaching model reloads. Parsing is pre-flight only, so that is usually acceptable.
- **Thinking mode:** keep `sop.think: false` (the default). Reasoning models must have thinking turned off for JSON output.
- **Test first:** parse one known SOP and compare the rules before switching.

## Configuration (`config.yaml`)

```yaml
database:
  url: postgresql://postgres:postgres@localhost:5432/postgres   # DATABASE_URL overrides
sop:
  model: ""            # empty = coach.model
  ollama_url: ""       # empty = coach.ollama_url
  think: false         # thinking OFF for reasoning models; "omit" = don't send the flag
  llm_timeout_s: 300
  num_ctx: 8192
  chunk_chars: 6000
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

`python -m sop.selftest` covers the validator, quote matching, units, dedupe, aircraft cross-checks, chunking and aircraft → profile matching, plus gaze rules, handbook chapters, book detection and the handbook prompt. It needs no Ollama, Postgres or PDF.

Live rule checking during a flight is not built yet: rules (including gaze rules) are stored, reviewed and loaded at lesson start, ready for the evaluator.
