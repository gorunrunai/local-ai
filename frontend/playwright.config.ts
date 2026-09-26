import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// E2E against the real backend + real local models. The backend serves the built app
// (run `npm run build` first) from a throwaway data directory on port 8766.
const PORT = 8766;
const dataDir = process.env.E2E_DATA_DIR ?? mkdtempSync(join(tmpdir(), "la-e2e-"));

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 180_000,
  expect: { timeout: 120_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure", viewport: { width: 1280, height: 860 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 860 } } }],
  webServer: {
    command: `uv run uvicorn orchestrator.app:app --host 127.0.0.1 --port ${PORT} --no-proxy-headers`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 240_000,
    reuseExistingServer: false,
    env: { DATA_DIR: dataDir, APP_PORT: String(PORT) },
  },
});
