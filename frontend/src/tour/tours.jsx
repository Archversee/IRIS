// Page tours: what each page's guided walk-through says and points at.
//
// A tour:
//   id      saved in the user's "seen" list -- rename it and everyone sees it again
//   title   shown small at the top of every step
//   match   ({pathname, search}) => true on the page(s) it belongs to
//   when    optional CSS selector that must be on screen (one URL, several screens)
//   roles   optional: only these roles get the tour
//   auto    false = never starts by itself, only from the "?" button
//   steps   [{ target?, title, body, roles? }]
//             target  CSS selector of what to light up; none = a centred card.
//                     Steps whose target isn't on screen are skipped, so a
//                     step can point at something that's only sometimes there.
//             title / body  text or JSX, or { trainee: ..., default: ... } to
//                     word it differently per role
//             roles   only show the step to these roles
//
// Prefer data-tour="..." attributes as targets (they survive restyling);
// existing class names are fine for parts of shared components.

const STAFF = ["instructor", "admin"];
const at = (path) => ({ pathname }) => pathname === path;
const sel = (name) => `[data-tour="${name}"]`;

// Last step of the first tour each role normally sees: where to find tours again.
const HELP_STEP = {
  target: ".sidebar__help",
  title: "See this again anytime",
  body: (
    <p>Every page with a tour has this <b>?</b> button. Click it to replay the tour for the page you're on.
      You can turn the automatic tours off, or start them all over, in <b>Settings › Help &amp; Guidance</b>.</p>
  ),
};

export const TOURS = [
  // ------------------------------------------------------------------ MAVIS
  {
    id: "walkaround",
    title: "Pre-flight walk-around",
    match: ({ pathname, search }) => pathname === "/live" && new URLSearchParams(search).has("reading"),
    when: sel("rl-head"),
    steps: [
      {
        title: { trainee: "Welcome! Let's start with the walk-around", default: "The pre-flight walk-around" },
        body: {
          trainee: <p>Before every flight, a pilot walks around the aircraft and checks it. This lesson teaches you
            those checks one at a time. It takes about a minute to learn how this page works.</p>,
          default: <p>The walk-around lesson trainees start with: a 3D aircraft with each pre-flight check marked on it,
            read aloud by MAVIS.</p>,
        },
      },
      {
        target: sel("rl-tabs"),
        title: "Two ways to learn the checks",
        body: <p><b>3D walk-around</b> shows the aircraft. <b>Checklist</b> shows the same checks as a list, which is
          handy for revising.</p>,
      },
      {
        target: sel("rl-viewer"),
        title: "Explore the aircraft",
        body: <p>Drag to look around. The highlighted points are the things to check. <b>Click a point</b> to open
          that check.</p>,
      },
      {
        target: sel("rl-card"),
        title: "What to check, and why",
        body: <p>The check you picked shows here. Press <b>🔊 Play</b> to hear MAVIS explain it.</p>,
      },
      {
        target: sel("rl-steps"),
        title: "Go through them in order",
        body: <p><b>Prev / Next</b> step through the checks in the order a pilot does them.
          <b> ▶ Play all</b> plays the whole walk-around hands-free.</p>,
      },
      {
        target: ".sidebar__section .sidebar__lesson-tree",
        title: "Your lessons",
        body: {
          trainee: <p>All the lessons are listed here under <b>Curriculum</b>. When you're done with the walk-around,
            pick the next lesson from this list.</p>,
          default: <p>The full curriculum. Pick any lesson here to open it.</p>,
        },
      },
      HELP_STEP,
    ],
  },
  {
    id: "basic-flight-menu",
    title: "Flight lessons",
    match: at("/live"),
    when: ".mavis-shell--menu .mavis-modes",
    steps: [
      {
        target: ".mavis-menu__greeting",
        title: "The lesson you're about to fly",
        body: <p>This is the lesson MAVIS has ready. To fly a different one, pick it from <b>Curriculum</b> in the
          sidebar.</p>,
      },
      {
        target: ".mavis-modes",
        title: "Choose how much help you want",
        body: <p>Each card is a different mode, with a different amount of coaching. Read the short description on
          each card, then <b>click one to start</b> the flight in the simulator.</p>,
      },
      {
        target: ".mavis-status-dot",
        title: "Is MAVIS ready?",
        body: <p><b>Green</b> means MAVIS (the AI instructor) is running and you can fly. <b>Red</b> means it isn't
          connected yet. Ask your instructor to start it.</p>,
      },
    ],
  },
  {
    id: "free-flight-menu",
    title: "Free Flight",
    match: at("/live/freeflight"),
    when: ".mavis-shell--menu .mavis-modes",
    steps: [
      {
        title: "Fly on your own",
        body: <p>Free Flight has no set lesson. You fly however you like and MAVIS keeps watching, so you still get a
          debrief afterwards.</p>,
      },
      {
        target: ".mavis-modes",
        title: "Pick a mode to start",
        body: <p>Click a card to start the flight in the simulator.</p>,
      },
      {
        target: ".mavis-status-dot",
        title: "Is MAVIS ready?",
        body: <p><b>Green</b> = connected and ready. <b>Red</b> = MAVIS isn't running yet.</p>,
      },
    ],
  },
  {
    // Never pops up by itself during a flight -- only from the "?" button.
    id: "in-flight",
    title: "During the flight",
    auto: false,
    match: ({ pathname }) => pathname === "/live" || pathname === "/live/freeflight",
    when: ".mavis-hud",
    steps: [
      {
        target: ".mavis-hud__left",
        title: "Your instructor",
        body: <p>MAVIS talks to you during the flight. Everything it says is written here too, and you can type a
          question when chat is on.</p>,
      },
      {
        target: ".mavis-hud__top",
        title: "Where you are in the lesson",
        body: <p>The current phase of flight (for example <i>Downwind</i>), which circuit you're on, and whether the
          SOP rule checks are running.</p>,
      },
      {
        target: ".mavis-hud__mapwrap",
        title: "Map and your next task",
        body: <p>The map shows where you are on the circuit. The card on top lists what to do next and ticks off
          each step as you do it.</p>,
      },
      {
        target: ".mavis-topbar",
        title: "End or restart",
        body: <p><b>End Session</b> finishes the flight and shows your score. <b>Restart</b> puts you back at the
          start of the lesson (that attempt isn't scored).</p>,
      },
    ],
  },
  {
    id: "leaderboard",
    title: "Leaderboard",
    match: at("/leaderboard"),
    when: ".mg-page",
    steps: [
      {
        target: ".mg-tabs",
        title: "Best scores",
        body: <p>Every flight flown with MAVIS, ranked by score. Use the buttons to switch between the
          <b> Visitor</b>, <b>Trainee</b> and <b>Pilot</b> modes, and the search box to find a pilot, lesson or
          aircraft.</p>,
      },
      {
        target: ".mg-lb",
        title: "Open a flight",
        body: <p>Each row is one flight. Use the buttons at the end of a row to open its review, or to delete
          one of your own runs.</p>,
      },
    ],
  },
  {
    id: "scoring-guide",
    title: "Scoring Guide",
    match: at("/scoring"),
    when: ".mg-scoring",
    steps: [
      {
        target: ".mg-scoring",
        title: "How flights are scored",
        body: <p>What MAVIS scores, how many points each part is worth, and what each grade means.
          Read this to understand your score after a flight.</p>,
      },
    ],
  },

  // ------------------------------------------------------------------- IRIS
  {
    id: "monitor-picker",
    title: "Live Monitor",
    match: at("/monitor"),
    when: ".lsp",
    steps: [
      {
        title: { trainee: "Watch a flight as it happens", default: "Watch your trainees fly, live" },
        body: <p>Live Monitor shows flights that are <b>in progress right now</b>: what the pilot sees,
          where they're looking, and the flight data. Finished flights move to <b>Sessions</b>.</p>,
      },
      {
        target: ".lsp-grid, .lsp-empty",
        title: "Pick a flight",
        body: <p>Each card is a simulator flying right now. <b>Click a card</b> to watch it. If nobody is flying,
          this page waits and checks every few seconds, so you don't need to refresh.</p>,
      },
      { ...HELP_STEP, roles: STAFF },
    ],
  },
  {
    id: "monitor-live",
    title: "Live Monitor",
    match: at("/monitor"),
    when: ".rev--live",
    steps: [
      {
        target: ".rev--live .rev-titlebar",
        title: "The flight you're watching",
        body: <p>Session name, aircraft and lesson. <b>● LIVE</b> means you're seeing the latest data. Rule alerts
          show up here as soon as a rule is broken.</p>,
      },
      {
        target: ".rev--live .rev-screens",
        title: "What the pilot sees",
        body: <p>The view out of the window, the instrument panel, and the ground track. The highlighted screen is
          the one the pilot is <b>looking at right now</b>. The dot is their gaze.</p>,
      },
      {
        target: ".rev--live .capture-btn",
        title: "No video showing?",
        body: <p>Press <b>◻ Capture</b> and choose the simulator's window to show it here. <b>■ Stop</b> ends
          the sharing.</p>,
      },
      {
        target: ".rev--live .rev-timeline",
        title: "The flight so far",
        body: <p>Altitude, speed and the other lines over time, with each phase of the lesson marked as a coloured
          box.</p>,
      },
      {
        target: ".rev--live .ft-card",
        title: "Instruments and limits",
        body: <p>Live readings. The <b>green band</b> on a gauge is the range allowed for this step of the lesson.
          A reading outside it turns amber.</p>,
      },
    ],
  },
  {
    id: "sessions",
    title: "Sessions",
    match: at("/sessions"),
    when: ".container .card",
    steps: [
      {
        title: { trainee: "Your finished flights", default: "Finished flights" },
        body: <p>Every flight ends up here once it's finished, newest first. Open one to see a full replay and
          debrief.</p>,
      },
      {
        target: sel("sessions-table"),
        title: "One row per flight",
        body: <p>Who flew, which lesson, which aircraft and when.</p>,
      },
      {
        target: sel("sessions-review"),
        title: "Open the debrief",
        body: <p><b>Review</b> opens the replay: video, gaze, flight data, the AI debrief and the rule checks.</p>,
      },
      {
        target: sel("sessions-delete"),
        title: "Careful with Delete",
        body: <p><b>Delete</b> removes the flight and its recordings for good. It asks first, and it can't be
          undone.</p>,
        roles: STAFF,
      },
    ],
  },
  {
    id: "review",
    title: "Flight review",
    match: ({ pathname }) => /^\/sessions\/[^/]+\/review$/.test(pathname),
    when: ".rev .rev-titlebar",
    steps: [
      {
        target: ".rev .rev-titlebar",
        title: "The flight at a glance",
        body: <p>Pilot, score, grade and lesson. The buttons on the right open the <b>Debrief</b> and the
          <b> SOP rule checks</b>.</p>,
      },
      {
        target: ".rev .rev-screens",
        title: "Replay what the pilot saw",
        body: <p>The view outside, the instrument panel and the ground track, all in sync. The dot is
          <b> where the pilot was looking</b>, and the screen they were looking at is highlighted.</p>,
      },
      {
        target: ".rev .tl-chart",
        title: "Move through the flight",
        body: <p><b>Click or drag on the chart</b> to jump to any moment. Altitude, speed and (if recorded) workload
          are drawn over time.</p>,
      },
      {
        target: ".rev .phase-buttons-row",
        title: "Each part of the lesson",
        body: <p>Each button is one step of the lesson, coloured by its grade. <b>Click one</b> to see how that
          part went.</p>,
      },
      {
        target: ".rev .tl-transport",
        title: "Play it back",
        body: <p>Play, pause, skip 10 seconds, or change the speed.</p>,
      },
      {
        target: ".rev .rev-bottom > :nth-child(1)",
        title: "Scan path",
        body: <p>The order the pilot looked at things (outside, airspeed, altimeter and so on). A good scan keeps
          coming back to the key instruments.</p>,
      },
      {
        target: ".rev .rev-bottom > :nth-child(2)",
        title: "Where the time went",
        body: <p>The share of time spent looking at each area, up to the current moment of the replay.</p>,
      },
      {
        target: "#rev-dock-debrief",
        title: "AI debrief",
        body: <p>A written summary of the whole flight: what went well and what to work on.</p>,
      },
      {
        target: "#rev-dock-rules",
        title: "SOP rule checks",
        body: {
          trainee: <p>Each rule from the handbook, and whether it was followed. Click a broken rule to jump to that
            moment and see why.</p>,
          default: <p>Each handbook rule and its result. Click a broken rule to jump to the moment. As the
            instructor, you can <b>overrule</b> a result, and your call is kept if the flight is checked
            again.</p>,
        },
      },
    ],
  },
  {
    id: "analytics",
    title: "Analytics",
    match: ({ pathname }) => /^\/sessions\/[^/]+\/analytics$/.test(pathname),
    when: ".container .card",
    steps: [
      {
        title: "The numbers behind a flight",
        body: <p>Flight and eye-tracking statistics for this session, and how long the pilot looked at each area.</p>,
      },
      {
        target: ".xc__grid, .xc__head",
        title: "Instrument cross-check",
        body: <p>How often the pilot moved their eyes between pairs of instruments. Moving between them often
          means a good cross-check. Pairs that are rarely or never checked are flagged.</p>,
      },
    ],
  },
  {
    id: "progress",
    title: "Progress",
    match: at("/progress"),
    when: ".pg-head",
    steps: [
      {
        title: { trainee: "How you're improving", default: "Progress across flights" },
        body: {
          trainee: <p>This page looks at all your flights together, so you can see what's getting better and what to
            practise next.</p>,
          default: <p>Trends across many flights: rule checks, the analysis of each broken rule, eye tracking,
            MAVIS's callouts and the flight data.</p>,
        },
      },
      {
        target: sel("pg-tabs"),
        title: "Two views",
        body: <p><b>Trainee</b> looks at one pilot in detail. <b>Instructor dashboard</b> compares everyone and shows
          which rules and callouts matter most.</p>,
        roles: STAFF,
      },
      {
        target: sel("pg-pick"),
        title: "Choose a trainee",
        body: <p>Pick whose flights to look at.</p>,
      },
      {
        target: sel("pg-insights"),
        title: "What stands out",
        body: <p>The most important things to know, in plain words. Start here.</p>,
      },
      {
        target: sel("pg-curves"),
        title: "Learning curves",
        body: <p>Each rule across each flight, oldest to newest. More passes towards the right means it's being
          learned.</p>,
      },
      {
        target: sel("pg-causes"),
        title: "Why rules were broken",
        body: <p>For each broken rule, what the analysis found: not seeing it in time, reacting late, or
          over-correcting.</p>,
      },
      {
        target: sel("pg-scan"),
        title: "Scan pattern",
        body: <p>How often each instrument was checked and how much time was spent looking outside, compared with an
          expert's flight.</p>,
      },
      {
        target: sel("pg-group"),
        title: "Everyone side by side",
        body: <p>All trainees compared. Useful for spotting who needs help, and with what.</p>,
        roles: STAFF,
      },
    ],
  },

  // ------------------------------------------------------------------ Tools
  {
    id: "upload",
    title: "Upload",
    match: at("/upload"),
    when: sel("up-session"),
    steps: [
      {
        title: "Add a flight by hand",
        body: <p>Flights flown with MAVIS are saved <b>automatically</b>. Use this page only for recordings made
          some other way. Work through the three boxes in order.</p>,
      },
      {
        target: sel("up-session"),
        title: "1. The session",
        body: <p>Pick an existing session, or fill in a name, pilot and aircraft and press <b>Create session</b>.</p>,
      },
      {
        target: sel("up-files"),
        title: "2. Data files",
        body: <p>The simulator's flight log and the eye tracker's export. Choose the files, then press
          <b> Upload &amp; ingest</b>.</p>,
      },
      {
        target: sel("up-videos"),
        title: "3. Videos",
        body: <p>Link the screen recordings to the session so they play in sync in the review.</p>,
      },
    ],
  },
  {
    id: "aoi-zones",
    title: "AOI Zones",
    match: at("/aoi-zones"),
    when: ".container .card",
    steps: [
      {
        title: "Mark where each instrument is",
        body: <p>An <b>AOI</b> (area of interest) is a box drawn around one instrument, like the airspeed
          indicator. The eye tracker then knows which instrument the pilot was looking at. You only need to do this
          when the panel layout changes.</p>,
      },
      {
        target: sel("aoi-session"),
        title: "1. Pick a recording",
        body: <p>Choose any session that has an instrument-panel video.</p>,
      },
      {
        target: sel("aoi-frame"),
        title: "2. Find a clear frame",
        body: <p>Slide to a moment where the panel is easy to see.</p>,
      },
      {
        target: sel("aoi-canvas"),
        title: "3. Draw the boxes",
        body: <p><b>Drag a rectangle</b> over each instrument, then name it (for example <i>Airspeed</i>).</p>,
      },
      {
        target: sel("aoi-zones"),
        title: "Your zones",
        body: <p>Every zone you've drawn. They're shared by all sessions.</p>,
      },
    ],
  },
  {
    id: "sop-new",
    title: "SOP Upload",
    match: at("/sop/new"),
    roles: STAFF,
    when: ".sop-head",
    steps: [
      {
        title: "Turn a handbook into flight rules",
        body: <p>Upload an aircraft SOP or a training handbook (PDF). It's read on this PC and turned into rules
          that MAVIS checks during and after every flight. Nothing is used until you review and save it.</p>,
      },
      {
        target: ".sop-health",
        title: "Local services",
        body: <p>The reading is done on this PC. Green chips mean the services it needs are running.</p>,
      },
      {
        target: ".sop-doctype",
        title: "What are you uploading?",
        body: <p>Say whether it's an aircraft's own handbook or a general training handbook. For a training
          handbook, you can also pick which one it is.</p>,
      },
      {
        target: ".sop-start",
        title: "Choose the PDF",
        body: <p>Choose the file to start. Big files take a few minutes. Processing pauses by itself while
          someone is flying, and carries on afterwards.</p>,
      },
      {
        target: ".sop-head .sop-link-btn",
        title: "Saved rules",
        body: <p>When it's done, review, edit and switch on the rules in <b>SOP Rules</b>.</p>,
      },
    ],
  },
  {
    id: "sop-rules",
    title: "SOP Rules",
    match: at("/sop/rules"),
    roles: STAFF,
    when: ".sop-head",
    steps: [
      {
        title: "The rules MAVIS checks",
        body: <p>Rules made from your uploaded SOPs and handbooks. MAVIS checks every flight against the
          <b> active</b> rules, live and again after landing, and the results show in each flight's review.</p>,
      },
      {
        target: ".sop-livechecks",
        title: "Live rule checks",
        body: <p>Choose whether rules are checked <b>during</b> circuit flights, not just afterwards. Changes apply
          from the next flight.</p>,
      },
      {
        target: ".sop-actabs",
        title: "One set per aircraft",
        body: <p>Choose an aircraft, or the general training handbook, to see its rules.</p>,
      },
      {
        target: ".sop-field",
        title: "Which rule set is active",
        body: <p>Only the <b>active</b> set is checked during flights. Switch sets here.</p>,
      },
      {
        target: ".sop-list",
        title: "Find a rule",
        body: <p>Search, or filter by tag. Click a rule to open it.</p>,
      },
      {
        target: ".sop-detail",
        title: "Read it in plain words",
        body: <p>Each rule is shown as a sentence, with the page of the handbook it came from. You can edit it or
          set it aside.</p>,
      },
    ],
  },
];
