import { copyFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";

// Builds the React-artifact runtime (loaded inside the sandboxed artifact frame) into
// public/artifact-runtime/, alongside local copies of Babel, Tailwind's browser build, Chart.js and D3.
// Nothing here is fetched from the internet at runtime.
const OUT = resolve(__dirname, "public/artifact-runtime");

export default defineConfig({
  publicDir: false,
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: OUT,
    emptyOutDir: true,
    lib: { entry: resolve(__dirname, "artifact-runtime/runtime.tsx"), formats: ["iife"], name: "ArtifactRuntime", fileName: () => "runtime.js" },
    minify: true,
    chunkSizeWarningLimit: 4000,
  },
  plugins: [{
    name: "copy-vendor",
    closeBundle() {
      mkdirSync(OUT, { recursive: true });
      copyFileSync(resolve(__dirname, "node_modules/@babel/standalone/babel.min.js"), resolve(OUT, "babel.min.js"));
      copyFileSync(resolve(__dirname, "node_modules/@tailwindcss/browser/dist/index.global.js"), resolve(OUT, "tailwind.js"));
      // HTML artifacts often load these from a CDN; the backend points those tags here instead.
      copyFileSync(resolve(__dirname, "node_modules/chart.js/dist/chart.umd.min.js"), resolve(OUT, "chart.js"));
      copyFileSync(resolve(__dirname, "node_modules/d3/dist/d3.min.js"), resolve(OUT, "d3.js"));
    },
  }],
});
