# SOP → flight rules

An instructor uploads an aircraft's Standard Operating Procedure (PDF). MAVIS reads it and a **local** LLM drafts machine-checkable flight rules. The instructor reviews and edits them, then saves them to the database as that aircraft's active rule set. Saved rules can be edited at any time, and every change is logged.

Everything runs on the sim laptop: PyMuPDF and Tesseract read the PDF, and Ollama drafts the rules. Nothing leaves the machine.

## Where it lives

The pages are in the IRIS React app (the dashboard at `http://localhost:3000`), under **MAVIS → Curriculum → Preflight & Cockpit**:

| Page | What it's for |
|---|---|
| **Upload SOP** — `/sop/new` | Upload → OCR / LLM → review and edit the drafted rules → **Save** |
| **SOP Rules** — `/sop/rules` | Pick an aircraft, then browse, edit, add, delete, or switch off its saved rules; see the change history; switch between rule sets |

The pages call MAVIS's `/sop/*` API through the `/mavis` proxy prefix. There is no PIN on these pages. The **Editing as** box in the page header records who made each change; it is remembered per browser.

## Upload SOP (`/sop/new`)

1. **Upload.** Drop the PDF. Status chips show whether the local LLM, OCR and the database are available, and whether a lesson is in progress (uploading waits until it ends).
2. **Processing.** A progress bar steps through: read pages (OCR for scanned ones) → draft rules → check rules. You can leave the page; processing continues on the MAVIS machine. The draft is listed under **Unsaved drafts** when you come back.
3. **Review and edit.** Nothing is in the database yet. The draft is a local file in `MAVIS/ai-instructor/data/sop/drafts/`, and every edit is autosaved to it. A refresh, a closed tab or a MAVIS restart loses nothing.
   - The left side lists the rules by SOP page, each with a plain-English summary (*"Hold indicated airspeed at 74 kt +10 / −5 kt during Climb out."*). Filters: **Needs fixing**, **Warnings**, **Set aside**.
   - The right side has the rule editor and the SOP page the rule came from. You can show the page image, with the quote highlighted, or the extracted text.
   - Untick a rule to **set it aside**, so it isn't saved. **+ Add rule** adds your own. **Run LLM again** re-drafts everything and keeps the rules you added by hand.
   - Keyboard: `J`/`K` move between rules, `X` includes or sets aside the current rule.
4. **Save.** Choose the aircraft and a rule set name, then press **Save N rules**. One transaction:
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

A **warning** doesn't block saving. Warnings are shown for:

- a number that doesn't appear in the quoted text;
- a quote that isn't word for word;
- a rule from an OCR'd page (compare it with the page image);
- a corrected page number;
- dropped phases.

## SOP Rules (`/sop/rules`)

- **Aircraft tabs** show each aircraft profile and its active rule set.
- **Rule set bar:** switch between the aircraft's sets (older ones are kept, inactive), **Rename**, **Make active** / **Deactivate**, and **Delete** (inactive sets only).
- **Rules:** edit in place, then **Save changes** or **Revert**. Switching away from unsaved edits asks first. The **On/Off** switch disables a rule without deleting it. You can also **+ Add rule** and **Delete rule**.
- An edit that would break a rule is refused with the reason, for example removing a band's tolerance.
- **History of this rule** / **All changes** show who changed what and when, with before → after values for edits.

## Which rules a flight uses

When a lesson starts, MAVIS picks the aircraft actually loaded in the simulator (`sop/runtime.py`):

1. MSFS reports `TITLE`, `ATC MODEL` and `ATC TYPE`.
2. These are matched against each profile's `sim_match` list (case-insensitive substring; the longest match wins).
3. MAVIS loads the **enabled rules of that aircraft's active rule set** into `state["sop"]`.

The React in-flight view shows a chip next to the phase, e.g. **SOP · C172 SOP rev3**.

```yaml
# profiles/aircraft/c172sp_g1000.yaml
sim_match: ["C172", "Cessna 172", "Skyhawk", "172SP"]
```

| Situation | Result |
|---|---|
| `sop.aircraft_override` is set in `config.yaml` | That profile's rules are used |
| The simulator reports nothing (mock, replay) | Config's `aircraft_profile` is used |
| The simulator reports an aircraft that matches **no** profile | **No rules are applied**; C172 rules are never used on another type |
| The aircraft has no active rule set | The lesson runs without SOP rules |

Loading never delays the start of a lesson.

To add an aircraft type: copy a profile YAML, set its name, speeds, flaps and `sim_match`, restart MAVIS, then upload its SOP.

## Database (`db/schema_sop_rules.sql`)

| Table | Holds |
|---|---|
| `sop_documents` | One row per saved PDF (SHA-256, OCR'd pages, local path) |
| `sop_pages` | Extracted text per page. It feeds the page view and the quote checks for later edits |
| `flight_rule_sets` | A named set of rules for one `aircraft_profile`. **At most one `active` per aircraft** (partial unique index) |
| `flight_rules` | One rule. See the rule types below. A `CHECK` constraint enforces each type's required values. Also stores `enabled`, provenance (`source_quote`, `source_page`, `quote_score`) and `llm_draft` (the LLM's original proposal, before any human edit) |
| `flight_rule_history` | Every create / update / delete / activate / deactivate / rename, with `changed_by`, `changed_at`, and full `before` / `after` JSON snapshots |

The rule types:

| `kind` | Values used | Meaning |
|---|---|---|
| `band` | `target`, `tol_hi`, `tol_lo` | Hold a target ± tolerance |
| `limit` | `min_value` / `max_value` | Stay within limits |
| `event` | `op`, `value`, `gate_phase` | Must be true by the start of a phase |
| `capture` | `capture_at`, `min_value` / `max_value` | Checked at lift-off, touchdown, … |
| `unobservable` | none | The instructor assesses it (radio calls, checklists) |

Any rule can also have:

- `phases`: the lesson phases it applies in (empty means every phase);
- `conditions`: `[{metric, op, value}]`, all of which must hold for the rule to apply.

All numbers are stored in MAVIS `Telemetry` units: kt, ft, ft/min, deg, rpm, and throttle 0–1.

Apply the schema once. It is idempotent and safe to re-run:

```
psql -U postgres -d postgres -f db/schema_sop_rules.sql
```

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

`python -m sop.selftest` covers the validator, quote matching, units, dedupe, aircraft cross-checks, chunking and aircraft → profile matching. It needs no Ollama, Postgres or PDF.
