// Ported verbatim from web/static/index.html's READING_3D -- per-checkpoint
// camera config, in the cessna.glb model's own axes (+X = aircraft's
// left, +Z = nose, +Y = up, roughly metres). Inert config data, not logic --
// see that file's own comments for the measured-landmark notes behind these
// numbers.
export const READING_3D = {
  preflight01_walkaround: {
    model: "/static/models/cessna.glb",
    camera: "orbit",
    blurb: "Preflight & Cockpit · walk the aircraft before you fly",
    frame: [6.65, 4.29, 8.26], // initial camera offset from the model centre (~30% closer than the fitted view)
    dist: [2.7, 41],
    grid: true,
    gridSize: 28,
    scale: 0.033,
    scaleByDistance: true,
    seeThrough: ["GlassMat_New"],
    hint: "Click a numbered point to walk round to it. Drag to rotate, scroll to zoom.",
    pos: {
      cabin: [-0.68, 1.65, -0.55], left_wing_root: [1.02, 2.24, -0.55], left_wing: [5.52, 2.24, -0.58],
      left_gear: [1.60, 0.49, -0.37], nose: [0, 1.39, 2.52], right_gear: [-1.60, 0.49, -0.37],
      right_wing: [-5.52, 2.24, -0.58], right_fuselage: [-0.64, 1.40, -1.60], empennage: [0, 2.38, -5.88],
      antennas: [0, 2.40, -1.20], final: [0, 0.17, -0.79],
    },
    view: {
      cabin: { pos: [-3.40, 1.75, 1.90], target: [-0.60, 1.60, -0.30] },
      left_wing_root: { pos: [3.24, 3.46, 1.62], target: [1.02, 2.18, -0.55] },
      left_wing: { pos: [7.84, 3.30, 1.69], target: [5.52, 2.18, -0.58] },
      left_gear: { pos: [4.60, 1.40, 1.90], target: [1.43, 0.35, -0.30] },
      nose: { pos: [2.11, 2.57, 5.65], target: [0, 1.32, 2.38] },
      right_gear: { pos: [-4.60, 1.40, 1.90], target: [-1.43, 0.35, -0.30] },
      right_wing: { pos: [-7.84, 3.30, 1.69], target: [-5.52, 2.18, -0.58] },
      right_fuselage: { pos: [-3.40, 1.90, -3.60], target: [-0.60, 1.40, -1.40] },
      empennage: { pos: [2.66, 3.56, -9.83], target: [0, 2.18, -5.88] },
      antennas: { pos: [6.20, 2.50, -2.20], target: [0, 2.10, -1.20] },
      final: { pos: [6.65, 4.29, 7.37], target: [0, 1.39, -2.09] },
    },
  },
  preflight02_cockpit: {
    model: "/static/models/cessna.glb",
    camera: "fixed",
    blurb: "Preflight & Cockpit · find your way around the controls",
    home: { pos: [0.00, 2.00, -0.75], target: [0.02, 1.55, 0.78] },
    dist: [0.1, 3],
    near: 0.01,
    fill: [0.10, 1.95, 0.30],
    grid: false,
    scale: 0.05,
    scaleByDistance: true,
    seeThrough: ["GlassMat_New"],
    hint: "Click a numbered point to move to that part of the cockpit. Drag to look around, scroll to zoom.",
    // pfd marks the six-pack flight instruments and mfd the radio stack on this
    // steam-gauge panel; the ids are kept from the G1000 version of the lesson.
    pos: {
      seating: [0.27, 1.20, 0.25], power_quadrant: [-0.035, 1.42, 0.65], ignition: [0.42, 1.41, 0.72],
      fuel_system: [0.01, 0.99, 0.58], pfd: [0.20, 1.69, 0.74], mfd: [-0.115, 1.65, 0.74],
      avionics: [0.24, 1.42, 0.73], lighting: [0.10, 1.43, 0.73], flaps_trim: [-0.20, 1.43, 0.73],
      final_check: [0.18, 1.30, 0.68],
    },
    view: {
      seating: { pos: [0.05, 1.85, 0.45], target: [0.27, 1.20, 0.00] },
      power_quadrant: { pos: [0.00, 1.75, 0.15], target: [-0.03, 1.42, 0.72] },
      ignition: { pos: [0.50, 1.60, 0.30], target: [0.44, 1.41, 0.74] },
      fuel_system: { pos: [0.00, 1.45, 0.35], target: [0.01, 0.96, 0.60] },
      pfd: { pos: [0.20, 1.72, 0.30], target: [0.20, 1.69, 0.78] },
      mfd: { pos: [-0.115, 1.72, 0.28], target: [-0.115, 1.65, 0.78] },
      avionics: { pos: [0.24, 1.50, 0.30], target: [0.24, 1.42, 0.76] },
      lighting: { pos: [0.10, 1.50, 0.33], target: [0.10, 1.42, 0.76] },
      flaps_trim: { pos: [-0.28, 1.40, 0.38], target: [-0.20, 1.43, 0.76] },
      final_check: { pos: [0.12, 1.88, -0.15], target: [0.05, 1.55, 0.78] },
    },
  },
};

export function readingCfg(lessonId) {
  return READING_3D[lessonId] || READING_3D.preflight01_walkaround;
}

// Materials named in cfg.seeThrough are authored opaque but stand for glass;
// draw them as a faint tint so what sits behind them stays visible.
export function applySeeThrough(root, names) {
  if (!names || !names.length) return;
  root.traverse((o) => {
    if (!o.isMesh) return;
    (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
      if (m && names.includes(m.name)) {
        m.transparent = true;
        m.opacity = 0.15;
        m.depthWrite = false;
      }
    });
  });
}
