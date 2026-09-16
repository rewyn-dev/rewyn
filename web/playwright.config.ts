import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the real console: the Python server serving
 * the built bundle over a real recorded run. Nothing is mocked, because the
 * thing being tested is that the whole path works (UI spec §57).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? "line" : "list",
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${process.env.CONSOLE_PORT ?? 4456}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "uv run --directory .. python scripts/console_fixture.py",
    url: `http://127.0.0.1:${process.env.CONSOLE_PORT ?? 4456}/console/v1/health`,
    // Never reuse a server: the fixture seeds a throwaway REWYN_HOME, and
    // tests write to it (replays, datasets). A fresh one per run is what makes
    // the suite deterministic.
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
