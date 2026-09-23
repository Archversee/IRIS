// Loads MAVIS's own vendored three.js build (proxied through /mavis) as
// plain <script> tags, same as the original app -- reusing the exact
// version/build MAVIS already ships rather than adding a separate npm
// dependency that could drift from it. Scripts attach THREE/OrbitControls/
// GLTFLoader as globals; this resolves once all three are ready.
let loadingPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded) return resolve();
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", reject);
      return;
    }
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => { el.dataset.loaded = "1"; resolve(); };
    el.onerror = reject;
    document.body.appendChild(el);
  });
}

export function loadThree() {
  if (!loadingPromise) {
    loadingPromise = loadScript("/mavis/static/vendor/three/three.min.js")
      .then(() => Promise.all([
        loadScript("/mavis/static/vendor/three/OrbitControls.js"),
        loadScript("/mavis/static/vendor/three/GLTFLoader.js"),
      ]))
      .then(() => window.THREE);
  }
  return loadingPromise;
}
