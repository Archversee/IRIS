import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { rmSync } from "node:fs";
import { resolve } from "node:path";

// public/media is also MAVIS's web.media_dir, so it can hold local-only
// sim clips (media/videos, ~900 MB each) -- keep those out of the build.
const dropMediaVideos = {
  name: "drop-media-videos",
  closeBundle() {
    rmSync(resolve(__dirname, "dist/media/videos"), { recursive: true, force: true });
  },
};

export default defineConfig({
  plugins: [react(), dropMediaVideos],
  server: {
    port: 5173,
    // Proxy /api -> FastAPI so the frontend can call same-origin in dev.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true, // needed for the live dashboard's WebSocket subscription
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      // static video files served by FastAPI's StaticFiles mount at /videos
      "/videos": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // MAVIS's own FastAPI+WS server (live coaching) -- proxied under
      // /mavis so the unified dashboard is same-origin here too, matching
      // the /mavis/* rule the production reverse proxy uses.
      "/mavis": {
        target: "http://localhost:6945",
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/mavis/, ""),
      },
    },
  },
});
