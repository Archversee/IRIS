// "Where to look" card for the flight familiarization lesson: a simplified
// G1000 PFD with the instrument(s) MAVIS is explaining lit up. Driven by
// state.look_at.items (attitude | airspeed | altitude | vsi | heading |
// outside), set from the lesson's step_notes[*].look.
const LABELS = {
  attitude: "Attitude indicator: the blue-and-brown picture in the middle",
  airspeed: "Airspeed: the tape on the left",
  altitude: "Altitude: the tape on the right",
  vsi: "Climb / descent: the thin scale right of the altitude",
  heading: "Heading: the compass circle at the bottom",
  outside: "Look outside: out of the window, not at the screen",
};

export default function LookCard({ lookAt }) {
  const items = lookAt?.items || [];
  if (!items.length) return null;
  const on = (id) => items.includes(id);
  const cls = (id) => "mavis-look__part" + (on(id) ? " is-on" : "");
  const outside = on("outside");

  return (
    <div className={"mavis-look" + (outside ? " mavis-look--outside" : "")}>
      <div className="mavis-look__title">{outside ? "Look outside" : "Look here"}</div>
      <svg viewBox="0 0 320 210" className="mavis-look__pfd" role="img"
        aria-label={"PFD with " + items.join(", ") + " highlighted"}>
        <rect x="1" y="1" width="318" height="208" rx="8" className="mavis-look__bezel" />
        {/* attitude: sky / ground / roll scale / aircraft symbol */}
        <g className={cls("attitude")}>
          <rect x="70" y="14" width="180" height="62" className="mavis-look__sky" />
          <rect x="70" y="76" width="180" height="62" className="mavis-look__ground" />
          <path d="M110 40 A60 60 0 0 1 210 40" className="mavis-look__line" />
          <path d="M160 24 l-5 8 h10 z" className="mavis-look__mark" />
          <path d="M128 76 h22 l10 8 l10 -8 h22" className="mavis-look__symbol" />
          <rect x="70" y="14" width="180" height="124" className="mavis-look__frame" />
        </g>
        {/* airspeed tape */}
        <g className={cls("airspeed")}>
          <rect x="14" y="14" width="48" height="124" className="mavis-look__tape" />
          <text x="38" y="81" className="mavis-look__num">95</text>
          <rect x="14" y="14" width="48" height="124" className="mavis-look__frame" />
        </g>
        {/* altitude tape */}
        <g className={cls("altitude")}>
          <rect x="258" y="14" width="44" height="124" className="mavis-look__tape" />
          <text x="280" y="81" className="mavis-look__num">2500</text>
          <rect x="258" y="14" width="44" height="124" className="mavis-look__frame" />
        </g>
        {/* vertical speed */}
        <g className={cls("vsi")}>
          <rect x="304" y="24" width="10" height="104" className="mavis-look__tape" />
          <rect x="304" y="24" width="10" height="104" className="mavis-look__frame" />
        </g>
        {/* heading / HSI */}
        <g className={cls("heading")}>
          <circle cx="160" cy="176" r="30" className="mavis-look__tape" />
          <text x="160" y="181" className="mavis-look__num">090</text>
          <circle cx="160" cy="176" r="30" className="mavis-look__frame" />
        </g>
      </svg>
      <ul className="mavis-look__labels">
        {items.map((id) => <li key={id}>{LABELS[id] || id}</li>)}
      </ul>
    </div>
  );
}
