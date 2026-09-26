import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

// The backend serves the built app on the same origin (needed for the PWA and for
// `tailscale serve`). In dev, Vite proxies /api to it.
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "favicon.ico", "apple-touch-icon.png"],
      manifest: {
        name: "GoRunRun Local AI",
        short_name: "GoRunRun",
        description: "Private multimodal assistant running on your Mac",
        theme_color: "#17171a",
        background_color: "#17171a",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // App shell only; never cache API calls or user files.
        navigateFallbackDenylist: [/^\/api\//],
        // Artifacts need the backend anyway; don't make phones pre-download the 5 MB runtime.
        globIgnores: ["**/artifact-runtime/**"],
        runtimeCaching: [],
        maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { chunkSizeWarningLimit: 4000, sourcemap: false },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
