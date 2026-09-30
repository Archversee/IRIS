// Ported verbatim from web/static/index.html's READING_3D -- per-checkpoint
// camera config, in the cockpit.glb model's own axes (+X = aircraft's
// left, +Z = nose, +Y = up). Inert config data, not logic -- see that
// file's own comments for the measured-landmark notes behind these numbers.
export const READING_3D = {
  lesson00_preflight: {
    model: "/static/models/cockpit.glb",
    camera: "orbit",
    blurb: "Preflight & Cockpit · walk the aircraft before you fly",
    frame: [2.3, 1.55, 2.8], // initial camera offset from the model centre (~20% closer than the fitted view)
    dist: [0.8, 12],
    grid: true,
    gridSize: 8,
    scale: 0.033,
    scaleByDistance: true,
    hint: "Click a numbered point to walk round to it. Drag to rotate, scroll to zoom.",
    pos: {
      cabin: [-0.20, 0.50, 0.35], left_wing_root: [0.30, 0.68, 0.35], left_wing: [1.62, 0.68, 0.34],
      left_gear: [0.47, 0.15, 0.40], nose: [0, 0.42, 1.24], right_gear: [-0.47, 0.15, 0.40],
      right_wing: [-1.62, 0.68, 0.34], right_fuselage: [-0.19, 0.45, -0.35], empennage: [0, 0.72, -1.20],
      antennas: [0, 0.72, -0.55], final: [0, 0.05, 0.28],
    },
    view: {
      cabin: { pos: [-0.88, 0.72, 0.95], target: [-0.18, 0.50, 0.35] },
      left_wing_root: { pos: [0.95, 1.05, 0.98], target: [0.30, 0.66, 0.35] },
      left_wing: { pos: [2.30, 1.00, 1.00], target: [1.62, 0.66, 0.34] },
      left_gear: { pos: [1.25, 0.45, 0.98], target: [0.44, 0.10, 0.40] },
      nose: { pos: [0.62, 0.78, 2.15], target: [0, 0.40, 1.20] },
      right_gear: { pos: [-1.25, 0.45, 0.98], target: [-0.44, 0.10, 0.40] },
      right_wing: { pos: [-2.30, 1.00, 1.00], target: [-1.62, 0.66, 0.34] },
      right_fuselage: { pos: [-0.90, 0.75, -0.78], target: [-0.19, 0.45, -0.35] },
      empennage: { pos: [0.78, 1.08, -2.35], target: [0, 0.66, -1.20] },
      antennas: { pos: [0.88, 1.28, -0.30], target: [0, 0.70, -0.58] },
      final: { pos: [1.95, 1.30, 2.65], target: [0, 0.42, -0.10] },
    },
  },
  lesson00_cockpit: {
    model: "/static/models/cockpit.glb",
    camera: "fixed",
    blurb: "Preflight & Cockpit · find your way around the controls",
    home: { pos: [0.00, 0.56, 0.14], target: [0.01, 0.475, 0.78] },
    dist: [0.06, 2],
    near: 0.01,
    fill: [0, 0.60, 0.55],
    grid: false,
    scale: 0.05,
    scaleByDistance: true,
    hint: "Click a numbered point to move to that part of the cockpit. Drag to look around, scroll to zoom.",
    pos: {
      seating: [0.09, 0.36, 0.46], power_quadrant: [0.00, 0.44, 0.70], ignition: [0.10, 0.44, 0.73],
      fuel_system: [0.00, 0.30, 0.68], pfd: [0.055, 0.49, 0.755], mfd: [-0.035, 0.49, 0.755],
      avionics: [-0.075, 0.455, 0.745], lighting: [0.09, 0.512, 0.745], flaps_trim: [-0.04, 0.42, 0.70],
      final_check: [0.075, 0.425, 0.655],
    },
    view: {
      seating: { pos: [0.00, 0.57, 0.26], target: [0.06, 0.40, 0.52] },
      power_quadrant: { pos: [0.00, 0.55, 0.45], target: [0.00, 0.435, 0.74] },
      ignition: { pos: [0.13, 0.50, 0.52], target: [0.10, 0.45, 0.74] },
      fuel_system: { pos: [0.15, 0.47, 0.42], target: [0.00, 0.30, 0.70] },
      pfd: { pos: [0.055, 0.52, 0.55], target: [0.055, 0.49, 0.76] },
      mfd: { pos: [-0.035, 0.52, 0.55], target: [-0.035, 0.49, 0.76] },
      avionics: { pos: [-0.075, 0.53, 0.53], target: [-0.075, 0.455, 0.76] },
      lighting: { pos: [0.11, 0.56, 0.53], target: [0.09, 0.512, 0.76] },
      flaps_trim: { pos: [0.05, 0.53, 0.45], target: [-0.04, 0.42, 0.72] },
      final_check: { pos: [0.00, 0.575, 0.20], target: [0.02, 0.455, 0.72] },
    },
  },
};

export function readingCfg(lessonId) {
  return READING_3D[lessonId] || READING_3D.lesson00_preflight;
}
