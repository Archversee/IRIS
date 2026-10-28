# Session Review: everything on one screen

The Debrief, SOP rule checks and Incidents used to open as slide-out drawers
from the chips in the title bar. They now sit permanently in a column on the
right of the Session Review page (/sessions/:id/review), stacked top to bottom:

1. Debrief      - AI summary + "the flight at a glance" tiles
2. SOP rule checks - "work on these first", not met / met / instructor-to-assess, sign-off
3. Incidents    - each broken rule; click one for the 30 s close-up (still a pop-up)

Each section scrolls on its own with its title pinned, so the page itself never
scrolls. The title-bar chips (SOP 2/2/1, Debrief, Incidents 3) stay as an
at-a-glance summary; clicking one now highlights its section instead of opening a drawer.
Below 900 px wide everything stacks into one scrolling column.

## Apply
Either copy `files/` over the repo root, or from the repo root:

    git apply session-review-one-screen.patch

Changed files (frontend only, no backend changes):
- frontend/src/pages/Review.jsx            - layout: docked right column, chips point at sections
- frontend/src/pages/review.css            - .rev--docked / .rev-dock / .rc-pane styles (drawer styles replaced)
- frontend/src/components/session/RuleChecksPanel.jsx - renders as a docked pane, no close button
- frontend/src/components/session/DebriefPanel.jsx    - same
- frontend/src/components/session/IncidentsPanel.jsx  - same

## Trade-off
The right column takes about a quarter of the width, so the three video
screens and the bottom cards are narrower than before (see screenshots/).
The column width is one line in review.css:
`.rev--docked { grid-template-columns: minmax(0, 1fr) clamp(340px, 27%, 520px); }`
and the height split between the three sections is `.rev-dock { grid-template-rows: ... }`.
