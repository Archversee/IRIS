# SOP → rules

An instructor uploads an aircraft's Standard Operating Procedure (PDF). MAVIS reads it and the local LLM drafts machine-checkable rules from it. The instructor verifies every rule, and the verified set is then bound to an aircraft profile. Everything runs on the sim laptop: PyMuPDF and Tesseract read the PDF, and Ollama drafts the rules. Nothing leaves the machine.

This feature builds the **bindings** step from [`acs-guided-instruction.md`](acs-guided-instruction.md) §3.2 (a human-reviewed bridge from a document to telemetry) for any SOP, not just the ACS. The rule kinds are the same, so a future `core/acs_eval.py` can evaluate SOP rules and ACS rules with one evaluator.

## Instructor workflow

1. Open the **Curriculum → Preflight & Cockpit → Aircraft SOPs** page. Anyone can see it: one card per aircraft profile, showing the names the simulator is recognised by, the active SOP, and what the last flight used. Drafts and older versions are listed below the cards. Press **Upload SOP**, or **Upload new version** on an aircraft's card, and enter the instructor PIN. The PIN session locks again when the wizard closes. Uploading is refused while a lesson is running.
2. **Upload & process.** Drop the PDF. The screen shows the state of the local services (database, LLM, OCR, no flight running) and a progress bar with three steps: read pages → draft rules → check rules. You can close the window while it runs; processing continues in the background.
3. **Verify.** Every drafted rule is listed by SOP page, with a plain-English summary. For example: *"Hold indicated airspeed at 74 kt +10 / −5 kt during Climb out."* Selecting a rule opens:
   - an editor for type, metric, numbers, phases, conditions and severity;
   - the **source quote**, with the original page image (quote highlighted) or the extracted text.

   To finish this step:
   - Approve, edit, or reject each rule. Keys: `J`/`K` move between rules, `A` approves, `R` rejects.
   - You can also add rules by hand.
   - **Approve all without warnings** approves in bulk, but only rules whose quote matches the SOP word for word and whose numbers appear in that quote.
   - **Mark as verified** stays disabled until every rule has a decision. Once verified, the rules are frozen; use **Revise** to create an editable copy as a new version.
4. **Bind to aircraft.** Pick a `profiles/aircraft/*.yaml`; the aircraft is preselected if you started from its card. MAVIS cross-checks the approved rules against that profile: speeds above Vne or below Vs0, flap values that aren't a real detent, and altitudes above the ceiling. Binding replaces whatever SOP that aircraft had before; each aircraft has one active SOP.

## Which SOP a flight uses

You can keep many SOPs: one is active per aircraft profile. When a lesson starts, `sop/runtime.py` picks the SOP for the aircraft actually being flown:

1. MAVIS asks the simulator what is loaded. For MSFS this is `TITLE`, `ATC MODEL` and `ATC TYPE`, read by `adapters/sim_msfs.py::aircraft_ident`.
2. The answer is matched against each profile's `sim_match` list (case-insensitive substring; the longest match wins, so "Skyhawk Classic" beats "Skyhawk"). Example for `profiles/aircraft/c172sp_g1000.yaml`:
   ```yaml
   sim_match: ["C172", "Cessna 172", "Skyhawk", "172SP"]
   ```
3. MAVIS loads the verified SOP bound to that profile. The SOP rules are kept in `sop_rt.active` for the in-flight evaluator. The result goes to the tablet as `state["sop"]` and shows as a chip next to the phase chip: **SOP · C172 SOP rev3 v2**.

The fallback rules:

| Situation | What happens |
|---|---|
| `sop.aircraft_override` is set in `config.yaml` | That profile's SOP is used, whatever the simulator reports |
| The simulator reports nothing (mock, replay, or a pysimconnect build that can't read string simvars) | Config's `aircraft_profile` is used |
| The simulator reports an aircraft that matches **no** profile | **No SOP is applied** (the chip reads *No SOP · unknown aircraft*); a C172 SOP must never be applied to another type |
| The matched profile has no verified SOP | The flight runs with none (the chip reads *No SOP for <aircraft>*) |

Loading never blocks the start of a lesson: it runs in the background with a 4 s database timeout.

**To add an aircraft type:** copy a profile YAML, set its `name`, speeds, flaps and `sim_match`, restart MAVIS, then upload and bind that aircraft's SOP.

**Still to confirm on the sim PC:** that pysimconnect returns `TITLE`/`ATC MODEL` there. The MAVIS log prints `sim aircraft: {...}` at lesson start. If the log shows nothing, set `sop.aircraft_override` meanwhile.

## Pipeline (`sop/`)

| Stage | File | What it does |
|---|---|---|
| Text extraction | `extract.py` | Reads the PDF page by page with PyMuPDF. OCR runs on a page if it has no text layer, or has thin text while mostly covered by images (a scanned sheet inside a typed document). OCR renders the page at 300 dpi and runs Tesseract. Pages are then packed into ~6 000-character chunks, each starting with a `[[PAGE n]]` marker. |
| Structured LLM parsing | `llm_parse.py` | Sends each chunk to Ollama with `format` set to a JSON schema (`schema.llm_json_schema`) and temperature 0. Decoding is constrained, so the model can only use known kinds, `Telemetry` metrics, lesson phases and units. The model reports numbers *as written*, with their unit; MAVIS converts them. |
| Validation | `validate.py` | Deterministic checks, described in the next section. This is where hallucinations are caught. |
| Database insertion | `store.py` | Writes to the `sop_*` tables in the shared Postgres (`db/schema_sop_rules.sql`). |
| Orchestration | `pipeline.py` | Runs one background job at a time. Parsing pauses between chunks while a flight is in progress, because it would compete with live coaching for the GPU. |
| API | `routes.py` | `/sop/*`. Every write requires the instructor PIN session. |

### What makes a rule auto-rejected vs. flagged

An **auto-rejected** rule is kept and shown collapsed in the wizard. It can only be restored once it passes these checks:

- its source quote cannot be found in the extracted SOP text (below 85 % token match);
- its unit doesn't fit its metric (for example, feet for airspeed);
- its kind is missing required fields (a band without a target, a limit without a min or max, …);
- a value is physically implausible for its metric.

A **flagged** rule can still be approved, but the reviewer sees a warning when:

- a number in the rule doesn't appear in its quote (signs are ignored, because MAVIS stores descent as negative);
- the quote matches the SOP but not word for word;
- the rule came from an OCR page;
- the page number was corrected;
- unknown phases were dropped.

Approving is also blocked for:

- a band with no tolerance;
- any rule that still fails the checks after editing.

### Rule kinds

All numbers are stored in `Telemetry` units (kt, ft, ft/min, deg, rpm, throttle 0–1).

| Kind | Fields | Example |
|---|---|---|
| `band` | `target`, `tol_hi`, `tol_lo` | Downwind 1 000 ft AGL ± 100 ft |
| `limit` | `min_value` and/or `max_value` | ≤ 85 kt while `flaps_deg ≥ 30` (a condition) |
| `event` | `op`, `value`, `gate_phase` | Flaps ≥ 30° by the start of FINAL |
| `capture` | `capture_at`, `min_value`/`max_value` | IAS ≥ 55 kt at lift-off |
| `unobservable` | none | Radio calls and checklists, for the instructor to assess |

Any rule can also have `phases` (empty means every phase) and `conditions` (`[{metric, op, value}]`).

### Runtime access

```python
from sop.store import RuleStore
active = await RuleStore(cfg["database"]["url"]).active_rules("c172sp_g1000")
# None, or {"ruleset_id", "version", "filename", "rules": [approved rules]}
```

The same data is available over HTTP at `GET /sop/active/{aircraft_profile}`, which needs no PIN because it is read-only. IRIS can read the `sop_*` tables directly. This first build only produces, verifies and binds rules; evaluating them during a flight is the next step (`core/acs_eval.py` in the ACS design).

## Setup (sim laptop, Windows)

1. Apply the migration once. It is additive and safe to re-run:
   `psql -U postgres -d postgres -f db/schema_sop_rules.sql`
2. Install the Python dependencies: `.venv\Scripts\pip install -r requirements.txt` (adds `pymupdf`, `pytesseract`, `pillow`, `asyncpg`).
3. For scanned SOPs, install **Tesseract OCR**; the UB Mannheim Windows build works. Then either add it to PATH or set `sop.tesseract_cmd` in `config.yaml` (e.g. `C:/Program Files/Tesseract-OCR/tesseract.exe`). Without Tesseract, text PDFs still work, and scanned pages are skipped with a warning.
4. No new model is needed. Parsing uses `coach.model` (`qwen2.5:14b-instruct`) unless `sop.model` is set. Ollama ≥ 0.5 is required for schema-constrained output; older versions fall back to plain JSON mode, and the validator still enforces the vocabulary.

Configuration lives in `config.yaml` under `database:` and `sop:`. The `DATABASE_URL` environment variable overrides `database.url`.

### Operational notes

- **Degrades gracefully.**
  - If Postgres is down, MAVIS still boots and flies, and the `/sop` endpoints answer 503.
  - If Ollama is down, the upload still extracts the PDF and creates an empty draft, so rules can be entered by hand and the SOP re-parsed later.
  - If the extra dependencies are missing, the SOP routes are simply not mounted.
- **Model reload after parsing.** Parsing uses `num_ctx: 8192`, while coaching calls use Ollama's default. Ollama reloads the model when this changes, so the first coaching line after a parse may take a few seconds and fall back to its template phrase. Parsing is pre-flight only.
- **Shared Postgres writer.** MAVIS writes only the `sop_*` tables. This is a deliberate, scoped exception to INTEGRATION_PLAN.md's Option A (IRIS as the sole Postgres writer): no IRIS-owned table is touched.
- **Duplicate uploads.** Re-uploading an identical PDF (same SHA-256) reuses its extracted text and creates a new ruleset version.

## Tests

- `python -m sop.selftest`: validator, quote matching, units, dedupe, aircraft checks, chunking, and aircraft → profile matching. Needs no Ollama, no Postgres and no PDF.
