# MAVIS `/live` native React rebuild — plan (proposed)

Source of truth for what's being ported: `MAVIS/ai-instructor/web/static/index.html`
(4011 lines, vanilla JS, no framework) + `web/server.py` (routes, unchanged by
this rebuild — the rebuild is frontend-only, talking to the same REST/WS API
MAVIS already exposes on :6945, proxied at `/mavis/*` per the integration plan).

This lives as a new route tree inside **IRIS's existing `frontend/`** (Vite +
React + react-router v6, already there) — not a separate app, per the
brief's "IRIS's frontend extended, not a fold-in of MAVIS's backend."

## Architecture decisions

1. **State**: one `useMavisSocket()` hook owns the WS connection (`/mavis/ws`
   in prod via the proxy, `ws://localhost:6945/ws` direct in dev — add a
   Vite proxy entry alongside the existing `/api`/`/videos` ones) and exposes
   the raw state via `useSyncExternalStore`, so components subscribe with
   selector hooks (`useMavisState(s => s.tel)`) and only re-render when their
   slice actually changes — replacing the original's hand-written
   `lastXSig`/`lastXSeq` guards with React's own memoization, field-for-field
   (每 signature-guard in the source report maps to one selector).
2. **REST**: a thin `mavisApi` client module (mirrors IRIS's existing
   `src/api/client.js` pattern) with one function per endpoint in the
   original's fetch table — same base-path-swap trick (`/mavis` prefix in
   prod, proxied in dev).
3. **Canvas map and the three.js 3D viewer stay imperative**, wrapped in
   thin React components that own a ref and replicate the draw calls /
   scene lifecycle in effects — porting the projection math and hotspot
   config near-verbatim is lower-risk than a declarative rewrite (SVG/R3F)
   for a first pass. Vendored three.js files copy over as static assets
   as-is.
4. **CSS**: port the ~800-line stylesheet close to verbatim into a scoped
   stylesheet (CSS module or a single `live.css` loaded only on this route,
   wrapped under one root class) so it can't leak into IRIS's existing
   Review/Analytics pages. Same two-axis theming contract (light/dark class
   + server-pushed `--acc/--good/--warn` accent vars) is preserved exactly.
5. **Mic recording**: port `recordMicWav()` as a shared hook
   (`useMicRecorder`) used by both voice-enrolment and push-to-talk, keeping
   `ScriptProcessorNode` for the first pass (matches current behavior
   exactly) rather than risking behavior drift by modernizing to
   `AudioWorklet` in the same slice.
6. **Local-only state** (settings, dialog queue, reading-lesson player,
   monitor widget geometry, optimistic reference selection) gets its own
   React state/context, matching the original's client-side ownership —
   none of this needs to move server-side or through IRIS's backend.

## Slices (smallest useful, most-valuable-first order)

Each slice ends in something you can click through and compare side-by-side
against the current `index.html` before the next one starts.

- **A — Plumbing.** New `/live` route, `useMavisSocket`, dev Vite proxy entry
  for MAVIS. Renders a raw JSON dump of live state. Proves the WS/REST path
  works end-to-end before any UI is built.
- **B — Splash + Main menu.** Wordmark splash, mode cards, trainee name
  input, lesson sidebar (accordion from `lesson_catalog`), `POST /session`.
  Gets you to the "in lesson" boundary.
- **C — In-flight HUD core.** Avatar box (video + TTS audio playback +
  autoplay-unlock), chat panel + text send, gauges (speed/alt/VS tapes),
  control bars (throttle/rudder/flaps), lesson strip (phase chip, reference
  picker). No map yet — the HUD is usable without it for a first review.
- **D — Canvas map.** The hand-rolled projection/track/zoom/hover-tooltip
  port — isolated because it's the single hardest piece.
- **E — Scorecard + leaderboard + scoring guide.** End-of-run debrief
  overlay, leaderboard popup, static scoring-guide content.
- **F — Settings + theming.** Slide-in settings panel, light/dark toggle,
  night mode, localStorage persistence.
- **G — Voice: enrolment + push-to-talk.** `useMicRecorder`, enrolment
  modal, PTT button + joystick-PTT mirror (`S.ctrl.ptt`).
- **H — Instructor area.** PIN gate, record-reference flow (note: "Save
  reference" is a stub in the original too — not a regression to fix here),
  manage runs, manage instructors.
- **I — Reading lessons + 3D viewer.** Checklist narration view, three.js
  walk-around/cockpit viewer with hotspots. Isolated last since it's a
  self-contained subsystem, not part of the core coaching loop.
- **J — Trainee-station monitor.** Draggable/resizable PiP video panel.
  Small; can fold into C or run standalone whenever convenient.

## Two things worth a decision before Slice A starts

1. **File layout** — propose `frontend/src/live/` (own subtree: `components/`,
   `hooks/`, `api/`, `live.css`) inside the existing IRIS frontend, kept
   separate from `components/session/` (the Review/LiveSession-monitor code)
   so the two don't tangle.
2. **Scope check** — Slices A–D get you a working, faithful live-coaching
   screen. E–J are real but lower-stakes (debrief, settings, voice enrol,
   instructor admin, reading lessons, PiP monitor). If you'd rather see the
   core loop sooner, I'd stop actively porting after D and revisit E–J once
   the rest of the integration (Postgres cutover, bridge, proxy) is in.

Ready to start on **Slice A** — say go and I'll build it, or adjust the
ordering/file-layout first if you'd rather.
