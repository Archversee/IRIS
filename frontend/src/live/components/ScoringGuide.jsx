// Static content ported from the original app's #guideMenuView, shown on
// the Scoring Guide page (pages/MavisGuides.jsx, opened from Settings).
export function ScoringGuideContent() {
  return (
    <>
      <p className="mavis-guide-intro">
        Each area is scored 0–100 from your live flying, then combined into a weighted total.
        The debrief is written by MAVIS; the scores are quantitative.
      </p>
      <ul className="mavis-guide-list">
        <li><b>Track</b> — how closely your green line followed the grey reference path.</li>
        <li><b>Speed</b> — how well you held each leg's target speed.</li>
        <li><b>Altitude</b> — holding circuit height on downwind and a steady glide on final.</li>
        <li><b>Landing</b> — touchdown softness, on the centreline, at the right speed (weighted most).</li>
        <li><b>Smoothness</b> — gentle, unhurried control inputs.</li>
      </ul>
      <p className="mavis-guide-intro">Your total becomes a letter grade:</p>
      <table className="mavis-guide-table">
        <thead><tr><th>Grade</th><th>A</th><th>B</th><th>C</th><th>D</th><th>F</th></tr></thead>
        <tbody><tr><td>Total</td><td>88+</td><td>76+</td><td>64+</td><td>50+</td><td>below 50</td></tr></tbody>
      </table>
      <p className="mavis-guide-intro">Retries (in <b>Trainee</b> and <b>Pilot</b> modes) cap your best possible total:</p>
      <table className="mavis-guide-table">
        <thead><tr><th>Retries</th><th>0</th><th>1</th><th>2</th><th>3</th><th>4+</th></tr></thead>
        <tbody><tr><td>Max total</td><td>100</td><td>85</td><td>78</td><td>70</td><td>60</td></tr></tbody>
      </table>
      <p className="mavis-guide-note">In Visitor and Exam modes retries are shown but don't cap your score.</p>
      <p className="mavis-guide-easter">There's a rare top mark above A — you'll know it if you earn it.</p>
    </>
  );
}
