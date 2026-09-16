import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { main } from "./helpers";

/**
 * The first screen, and the demo that makes the console legible before it is
 * useful (UI spec §41, §47, §59).
 *
 * The demo tests load and then remove the demo project against the running
 * fixture. That is safe because every demo run is tagged and removal is
 * scoped to the tag — which is the property worth testing, so the test is
 * also the proof.
 */

test.describe("the first screen (UI §47)", () => {
  test("says what the tool is, where you are, and what a project is", async ({ page }) => {
    await page.goto("/welcome");
    await expect(page.getByRole("heading", { name: "Rewyn", level: 1 })).toBeVisible();
    await expect(page.getByText(/records what your AI actually did/)).toBeVisible();

    await expect(page.getByRole("heading", { name: "Where you are" })).toBeVisible();
    await expect(page.getByText(/nothing to create/)).toBeVisible();
  });

  test("hands you a first run that is runnable rather than a placeholder", async ({ page }) => {
    await page.goto("/welcome");
    const code = main(page).locator("pre").first();
    await expect(code).toBeVisible();
    // The snippet §47 asks for must produce a run, so it needs a real agent.
    await expect(main(page).getByText(/Agent\(/)).toBeVisible();
    await expect(main(page).getByText(/\.\.\./)).toHaveCount(0);
  });

  test("names the four jobs the tool is for, each linking to its screen", async ({ page }) => {
    await page.goto("/welcome");
    await expect(page.getByRole("heading", { name: "What this is for" })).toBeVisible();
    for (const job of [
      "Debug an answer you do not trust",
      "Prove a change did not make it worse",
      "Find what changed outside your code",
      "Understand what it costs",
    ]) {
      await expect(page.getByText(job)).toBeVisible();
    }
    await page.getByText("Debug an answer you do not trust").click();
    await expect(page).toHaveURL(/\/runs$/);
  });

  test("is reachable from the project block in the sidebar", async ({ page }) => {
    await page.goto("/runs");
    await page.locator(".project").click();
    await expect(page).toHaveURL(/\/welcome$/);
  });

  test("has no detectable accessibility violations", async ({ page }) => {
    await page.goto("/welcome");
    await page.waitForSelector("main");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });
});

test.describe.serial("the demo project", () => {
  test.afterAll(async ({ request }) => {
    // Leave the fixture exactly as it was found, whatever happened above.
    await request.delete("/console/v1/demo");
  });

  test("loads, fills the console, and comes out without touching your runs", async ({
    page,
    request,
  }) => {
    const before = await (await request.get("/console/v1/runs?limit=200")).json();
    const mine: string[] = before.runs.map((run: { id: string }) => run.id);

    await page.goto("/welcome");
    await page.getByRole("button", { name: "Load the demo project" }).click();
    // Recording the demo takes a moment; the button flipping is the signal
    // that the server finished, not the click.
    await expect(page.getByRole("button", { name: "Remove the demo data" })).toBeVisible({
      timeout: 30_000,
    });

    const loaded = await (await request.get("/console/v1/onboarding")).json();
    expect(loaded.demo_loaded).toBe(true);
    expect(loaded.counts.agents).toBeGreaterThanOrEqual(2);
    expect(loaded.counts.versions).toBeGreaterThanOrEqual(2);
    expect(loaded.counts.failures).toBeGreaterThanOrEqual(3);

    // The screens the demo exists to light up are lit.
    const incidents = await (await request.get("/console/v1/incidents")).json();
    expect(incidents.length).toBeGreaterThan(0);

    await request.delete("/console/v1/demo");
    const after = await (await request.get("/console/v1/runs?limit=200")).json();
    const remaining: string[] = after.runs.map((run: { id: string }) => run.id);
    for (const id of mine) expect(remaining).toContain(id);
  });
});
