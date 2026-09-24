import { useEffect, useRef, useState } from "react";
import { loadThree } from "./loadThree.js";
import { readingCfg } from "./readingConfig.js";

// Ported from the original app's ensurePf3D()/buildHotspots()/animate()
// (web/static/index.html). Same scene setup, same numbered-badge hotspots,
// same raycast-click-to-select, same camera easing to a picked checkpoint's
// viewpoint. Reuses MAVIS's own vendored three.js build via loadThree().
function makeStepLabel(THREE, n) {
  const size = 96;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
  ctx.fillStyle = "#4da3ff";
  ctx.fill();
  ctx.strokeStyle = "#04101f";
  ctx.lineWidth = 4;
  ctx.stroke();
  ctx.fillStyle = "#04101f";
  ctx.font = "bold 44px Arial,sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(n), size / 2, size / 2 + 2);
  const tex = new THREE.CanvasTexture(c);
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, depthWrite: false, transparent: true }));
  spr.scale.set(0.46, 0.46, 1);
  return spr;
}

export default function CockpitViewer({ lessonId, checkpoints, selectedId, onPick }) {
  const containerRef = useRef(null);
  const pf3DRef = useRef(null);
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;
  const [error, setError] = useState(null);

  // Build the scene once per lesson (model swap needs a fresh scene).
  useEffect(() => {
    let cancelled = false;
    let resizeHandler = null;

    loadThree()
      .then((THREE) => {
        if (cancelled) return;
        const cfg = readingCfg(lessonId);
        const container = containerRef.current;
        if (!container) return;
        const w = Math.max(container.clientWidth, 300), h = container.clientHeight || 420;

        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0b1220);
        const camera = new THREE.PerspectiveCamera(45, w / h, cfg.near || 0.1, 200);
        camera.position.set(9, 6, 11);
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(w, h);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        container.innerHTML = "";
        container.appendChild(renderer.domElement);

        const controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.minDistance = (cfg.dist || [3, 40])[0];
        controls.maxDistance = (cfg.dist || [3, 40])[1];

        scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.4));
        const sun = new THREE.DirectionalLight(0xffffff, 1.1);
        sun.position.set(6, 10, 4);
        scene.add(sun);
        if (cfg.fill) {
          const fill = new THREE.PointLight(0xffffff, 1.2, 3);
          fill.position.set(...cfg.fill);
          scene.add(fill);
        }
        const gs = cfg.gridSize || 30;
        const grid = new THREE.GridHelper(gs, gs, 0x2a3a4a, 0x1a2530);
        grid.visible = cfg.grid !== false;
        scene.add(grid);

        const hotspots = [];
        const raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
        const pickAt = (clientX, clientY) => {
          const rect = renderer.domElement.getBoundingClientRect();
          mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1;
          mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1;
          raycaster.setFromCamera(mouse, camera);
          const hits = raycaster.intersectObjects(hotspots.map((h) => h.mesh));
          return hits.length ? hotspots.find((h) => h.mesh === hits[0].object) : null;
        };
        renderer.domElement.addEventListener("click", (e) => {
          const hit = pickAt(e.clientX, e.clientY);
          if (hit) onPickRef.current?.(hit.id);
        });
        renderer.domElement.addEventListener("mousemove", (e) => {
          renderer.domElement.style.cursor = pickAt(e.clientX, e.clientY) ? "pointer" : "grab";
        });

        const pf3D = { scene, camera, renderer, controls, hotspots, container, animId: 0, goal: null, THREE };
        pf3DRef.current = pf3D;

        function animate() {
          pf3D.animId = requestAnimationFrame(animate);
          const t = performance.now() * 0.0025;
          hotspots.forEach((hh) => {
            let scale = hh.baseScale * (hh.selected ? 1.3 : 1);
            if (hh.isNext) scale *= 1 + 0.14 * Math.sin(t * 3);
            if (cfg.scaleByDistance) scale *= camera.position.distanceTo(hh.mesh.position);
            hh.mesh.scale.setScalar(scale);
          });
          if (pf3D.goal) {
            camera.position.lerp(pf3D.goal.pos, 0.12);
            controls.target.lerp(pf3D.goal.target, 0.12);
            if (camera.position.distanceTo(pf3D.goal.pos) < 0.01) pf3D.goal = null;
          }
          controls.update();
          renderer.render(scene, camera);
        }
        pf3D.animate = animate;

        const loadingEl = document.createElement("div");
        loadingEl.className = "mavis-pf3d-loading";
        loadingEl.textContent = "Loading 3D model…";
        container.appendChild(loadingEl);

        new THREE.GLTFLoader().load(
          "/mavis" + cfg.model,
          (gltf) => {
            if (cancelled) return;
            scene.add(gltf.scene);
            const box = new THREE.Box3().setFromObject(gltf.scene);
            const center = box.getCenter(new THREE.Vector3());
            if (cfg.home) {
              camera.position.set(...cfg.home.pos);
              controls.target.set(...cfg.home.target);
            } else {
              const f = cfg.frame || [9, 6, 11];
              controls.target.copy(center);
              camera.position.set(center.x + f[0], center.y + f[1], center.z + f[2]);
            }
            camera.lookAt(controls.target);
            grid.position.y = box.min.y;
            const scale = cfg.scale || 0.46;
            (checkpoints || []).forEach((cp, i) => {
              const pos = cfg.pos[cp.id];
              if (!pos) return;
              const label = makeStepLabel(THREE, i + 1);
              label.position.set(...pos);
              scene.add(label);
              hotspots.push({ id: cp.id, mesh: label, baseScale: scale, selected: cp.id === selectedId, isNext: i === 0 });
            });
            loadingEl.remove();
          },
          undefined,
          (err) => {
            console.error("Cockpit model failed to load", err);
            loadingEl.textContent = "Couldn't load the 3D model.";
          }
        );

        resizeHandler = () => {
          if (!container.clientWidth) return;
          camera.aspect = container.clientWidth / (container.clientHeight || 420);
          camera.updateProjectionMatrix();
          renderer.setSize(container.clientWidth, container.clientHeight || 420);
        };
        window.addEventListener("resize", resizeHandler);
        animate();
      })
      .catch(() => {
        if (!cancelled) setError("3D viewer failed to load (three.js missing) -- use the list view.");
      });

    return () => {
      cancelled = true;
      if (resizeHandler) window.removeEventListener("resize", resizeHandler);
      if (pf3DRef.current) {
        cancelAnimationFrame(pf3DRef.current.animId);
        pf3DRef.current.renderer.dispose();
        pf3DRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lessonId]);

  // Drive hotspot selection + camera easing when the selected checkpoint changes.
  useEffect(() => {
    const pf3D = pf3DRef.current;
    if (!pf3D) return;
    const idx = (checkpoints || []).findIndex((c) => c.id === selectedId);
    const nextId = idx > -1 && idx + 1 < checkpoints.length ? checkpoints[idx + 1].id : null;
    pf3D.hotspots.forEach((h) => {
      h.selected = h.id === selectedId;
      h.isNext = h.id === nextId;
    });
    const view = (readingCfg(lessonId).view || {})[selectedId];
    if (view) {
      pf3D.goal = { pos: new pf3D.THREE.Vector3(...view.pos), target: new pf3D.THREE.Vector3(...view.target) };
    }
  }, [selectedId, checkpoints, lessonId]);

  if (error) return <p className="muted" style={{ padding: 20 }}>{error}</p>;
  return <div className="mavis-pf3d" ref={containerRef} />;
}
