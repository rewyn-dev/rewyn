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
  // The console picks its theme from prefers-color-scheme, so a suite that
  // runs in one theme only ever exercises half the palette -- which is how a
  // light-mode contrast failure reached CI while passing on a developer's
  // machine in dark mode. The full suite runs light; the accessibility checks
  // run again dark. Only they depend on the palette, and the two projects
  // share one seeded server, so re-running the state-writing tests would have
  // them trip over what the first pass wrote.
  projects: [
    { name: "chromium-light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
    {
      name: "chromium-dark",
      grep: /violations/,
      use: { ...devices["Desktop Chrome"], colorScheme: "dark" },
    },
  ],
  webServer: {
    command: "uv run --all-extras --directory .. python scripts/console_fixture.py",
    url: `http://127.0.0.1:${process.env.CONSOLE_PORT ?? 4456}/console/v1/health`,
    // Never reuse a server: the fixture seeds a throwaway REWYN_HOME, and
    // tests write to it (replays, datasets). A fresh one per run is what makes
    // the suite deterministic.
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
