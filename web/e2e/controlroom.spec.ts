import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { main, openDemoRun } from "./helpers";

/**
 * The control room: incidents, the workspace, collaboration and the
 * AI-assisted explanation (UI spec §23, §36, §37, §45, §60).
 *
 * These are the screens where the product makes claims about causes and
 * about where work stands, so the tests check the hedging as much as the
 * rendering: an unreached stage says what would reach it, and an explanation
 * says who wrote it.
 */

test.describe("incidents (UI §36)", () => {
  test("a failure cluster becomes an incident with the seven-step timeline", async ({ page }) => {
    await page.goto("/incidents");
    const incident = page.getByText(/runs failing: RuntimeError/).first();
    await expect(incident).toBeVisible();
    await incident.click();

    await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
    for (const stage of [
      "Deployment",
      "Behavior change",
      "Detection",
      "Investigation",
      "Fix",
      "Regression",
      "Resolved",
    ]) {
      await expect(main(page).getByText(stage, { exact: true })).toBeVisible();
    }
  });

  test("an unreached stage says what would reach it", async ({ page }) => {
    await page.goto("/incidents");
    await page
      .getByText(/runs failing: RuntimeError/)
      .first()
      .click();
    await expect(page.getByText(/No regression report covers this agent/)).toBeVisible();
    await expect(page.getByText(/Nobody has taken this yet/)).toBeVisible();
  });

  test("a cause is labelled, never asserted", async ({ page }) => {
    await page.goto("/incidents");
    await page
      .getByText(/runs failing: RuntimeError/)
      .first()
      .click();
    await expect(page.getByRole("heading", { name: "Likely cause" })).toBeVisible();
    await expect(main(page).getByText("Observed", { exact: true }).first()).toBeVisible();
  });

  test("taking one moves it along its own timeline", async ({ page }) => {
    await page.goto("/incidents");
    await page
      .getByText(/runs failing: RuntimeError/)
      .first()
      .click();

    await page.getByLabel("Assign to").fill("raj");
    await page.getByRole("button", { name: "Assign", exact: true }).click();

    await expect(page.getByText("raj is on this.")).toBeVisible();
    await expect(page.getByText("assigned to raj")).toBeVisible();
  });
});

test.describe("the AI development workspace (UI §37, §60)", () => {
  test("puts config, runs, inspection, replay, evaluation and regression in one frame", async ({
    page,
  }) => {
    await page.goto("/workspace?agent=acme-credit");
    await expect(page.getByRole("heading", { name: "acme-credit", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Configuration" })).toBeVisible();
    const lifecycle = page.locator(".workspace-stages");
    await expect(lifecycle.getByRole("heading", { name: "Lifecycle" })).toBeVisible();
    for (const band of ["Agent configuration", "Replay", "Evaluation", "Regression"]) {
      await expect(lifecycle.getByText(band, { exact: true })).toBeVisible();
    }
  });

  test("draws the loop, and every step of it is a link", async ({ page }) => {
    await page.goto("/workspace?agent=acme-credit");
    const loop = page.getByRole("navigation", { name: "Development loop" });
    await expect(loop).toBeVisible();
    await expect(loop.getByRole("link")).toHaveCount(9);

    await loop.getByRole("link", { name: /Monitor/ }).click();
    await expect(page).toHaveURL(/\/(incidents)?$/);
  });

  test("asks for an agent rather than guessing one", async ({ page }) => {
    await page.goto("/workspace");
    await expect(page.getByRole("heading", { name: "Pick an agent" })).toBeVisible();
  });
});

test.describe("collaboration (UI §45)", () => {
  test("a note on a run is kept, with who wrote it", async ({ page }) => {
    await openDemoRun(page);
    await page.getByPlaceholder("Add a note…").fill("this is the one that took 4s");
    await page.getByRole("button", { name: "Comment" }).click();
    await expect(page.getByText("this is the one that took 4s")).toBeVisible();

    await page.reload();
    await expect(page.getByText("this is the one that took 4s")).toBeVisible();
  });

  test("a filter can be kept and reopened", async ({ page }) => {
    await page.goto("/runs?status=failed");
    await page.getByRole("button", { name: "Save this view" }).click();
    await page.getByLabel("Name this view").fill("Failing refunds");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await page.goto("/runs");
    await expect(page.getByRole("link", { name: "Failing refunds" })).toBeVisible();
    await page.getByRole("link", { name: "Failing refunds" }).click();
    await expect(page).toHaveURL(/status=failed/);
  });
});

test.describe("AI-assisted debugging (UI §23)", () => {
  test("explains a difference from the comparison's evidence, and says who wrote it", async ({
    page,
  }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Compare/ })
      .click();
    await page.getByText("previous successful run").click();

    await expect(page.getByRole("heading", { name: "Explain difference" })).toBeVisible();
    await page.getByRole("button", { name: "Explain difference", exact: true }).click();

    await expect(page.getByText("composed by rule")).toBeVisible();
    await expect(page.getByText(/REWYN_EXPLAIN_MODEL/)).toBeVisible();
  });
});

test.describe("accessibility of the P4 screens (UI §49)", () => {
  for (const path of ["/incidents", "/workspace?agent=acme-credit"]) {
    test(`${path} has no detectable violations`, async ({ page }) => {
      await page.goto(path);
      await page.waitForSelector("main");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(results.violations).toEqual([]);
    });
  }
});
