import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy /api -> FastAPI so the frontend can call same-origin in dev.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      // static video files served by FastAPI's StaticFiles mount at /videos
      "/videos": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
