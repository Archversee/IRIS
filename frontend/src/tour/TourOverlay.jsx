import { isValidElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

// The tour itself: dims the page, cuts a lit "window" around the element a
// step is about, and shows the step's card next to it. Steps whose element
// isn't on screen (e.g. an instructor-only button for a trainee, an empty
// list) are skipped; a step with no `target` is a centred card.
// Keys: → / Enter next, ← back, Esc closes.

const PAD = 6; // space between the element and its lit window
const GAP = 12; // space between the lit window and the card
const CARD_W = 360;
const MARGIN = 12; // keep the card this far from the window edges

// Text, JSX, or { trainee: ..., default: ... } worded per role.
function textFor(v, role) {
  if (v == null || typeof v !== "object" || isValidElement(v)) return v;
  return v[role] ?? v.default;
}

function visibleEl(sel) {
  if (!sel) return null;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

function place(rect, card) {
  const vw = window.innerWidth, vh = window.innerHeight;
  const w = Math.min(CARD_W, vw - 2 * MARGIN);
  const h = card?.offsetHeight || 200;
  if (!rect) return { left: (vw - w) / 2, top: Math.max(MARGIN, (vh - h) / 2), width: w };
  const clampX = (x) => Math.min(Math.max(MARGIN, x), vw - w - MARGIN);
  const clampY = (y) => Math.min(Math.max(MARGIN, y), vh - h - MARGIN);
  const below = rect.bottom + PAD + GAP, above = rect.top - PAD - GAP - h;
  const right = rect.right + PAD + GAP, left = rect.left - PAD - GAP - w;
  if (below + h <= vh - MARGIN) return { left: clampX(rect.left), top: below, width: w };
  if (above >= MARGIN) return { left: clampX(rect.left), top: above, width: w };
  if (right + w <= vw - MARGIN) return { left: right, top: clampY(rect.top), width: w };
  if (left >= MARGIN) return { left, top: clampY(rect.top), width: w };
  return { left: clampX(rect.left), top: vh - h - MARGIN, width: w }; // huge element: sit over its bottom
}

export default function TourOverlay({ tour, role, onDone }) {
  const steps = useMemo(() => tour.steps.filter((s) => !s.roles || s.roles.includes(role)), [tour, role]);
  const [i, setI] = useState(0);
  const [rect, setRect] = useState(null);
  const [dontShow, setDontShow] = useState(false);
  const cardRef = useRef(null);
  const [pos, setPos] = useState(null);
  const step = steps[i];

  // Skip forward/back past steps whose element isn't there.
  const go = useCallback((dir) => {
    let j = i + dir;
    while (j >= 0 && j < steps.length && steps[j].target && !visibleEl(steps[j].target)) j += dir;
    if (j >= steps.length) onDone({ disableAll: dontShow });
    else if (j >= 0) setI(j);
  }, [i, steps, onDone, dontShow]);

  // First step may itself be missing.
  useEffect(() => {
    if (step?.target && !visibleEl(step.target)) go(1);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Bring the element into view, then follow it (scrolling, resizing, layout changes).
  useEffect(() => {
    const el = visibleEl(step?.target);
    if (!el) { setRect(null); return undefined; }
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
    let raf;
    let prev = "";
    const track = () => {
      const r = el.getBoundingClientRect();
      const k = `${r.left},${r.top},${r.width},${r.height}`;
      if (k !== prev) { prev = k; setRect({ left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }); }
      raf = requestAnimationFrame(track);
    };
    track();
    return () => cancelAnimationFrame(raf);
  }, [step]);

  useLayoutEffect(() => { setPos(place(rect, cardRef.current)); }, [rect, i]);
  useEffect(() => { cardRef.current?.focus(); }, [i]);

  useEffect(() => {
    const onKey = (e) => {
      // capture phase + stopPropagation: the page under the tour (e.g. the
      // walk-around's own Prev/Next) doesn't also react to these keys
      if (!["Escape", "ArrowRight", "ArrowLeft"].includes(e.key)) return;
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") onDone({ disableAll: dontShow });
      else go(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [go, onDone, dontShow]);

  if (!step) return null;
  // Count and dots cover only the steps that can show on this screen right
  // now (an empty page skips the steps about its contents).
  const live = steps.map((s, k) => k).filter((k) => k === i || !steps[k].target || visibleEl(steps[k].target));
  const stepNo = live.indexOf(i);
  const last = stepNo === live.length - 1;
  const hole = rect && {
    left: rect.left - PAD, top: rect.top - PAD, width: rect.width + 2 * PAD, height: rect.height + 2 * PAD,
  };

  return (
    <div className="tour" role="presentation">
      {/* Clicks on the dimmed page are caught here, so the page can't be used mid-tour. */}
      <div className="tour__catch" />
      {hole
        ? <div className="tour__hole" style={hole} />
        : <div className="tour__dim" />}
      <div ref={cardRef} className="tour__card" role="dialog" aria-modal="true" aria-labelledby="tour-title"
        aria-describedby="tour-body" tabIndex={-1} style={pos || { visibility: "hidden" }}>
        <div className="tour__meta">
          <span>{tour.title}</span>
          <span>{stepNo + 1} / {live.length}</span>
        </div>
        <h3 id="tour-title" className="tour__title">{textFor(step.title, role)}</h3>
        <div id="tour-body" className="tour__body">{textFor(step.body, role)}</div>
        <div className="tour__dots" aria-hidden="true">
          {live.map((k) => <i key={k} className={k === i ? "is-on" : k < i ? "is-done" : ""} />)}
        </div>
        <div className="tour__foot">
          {stepNo === 0 ? (
            <label className="tour__dont">
              <input type="checkbox" checked={dontShow} onChange={(e) => setDontShow(e.target.checked)} />
              Don't show tours
            </label>
          ) : (
            <button type="button" className="ghost" onClick={() => go(-1)}>‹ Back</button>
          )}
          <span className="spacer" />
          {!last && <button type="button" className="ghost" onClick={() => onDone({ disableAll: dontShow })}>Skip</button>}
          <button type="button" onClick={() => go(1)}>{last ? "Done" : "Next ›"}</button>
        </div>
      </div>
    </div>
  );
}
