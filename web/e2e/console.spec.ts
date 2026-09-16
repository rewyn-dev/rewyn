import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { openDemoRun } from "./helpers";

/** Wait for the bundle to hydrate before driving it from the keyboard. */
async function ready(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await page.waitForSelector("main");
  await page.getByRole("button", { name: /Search/ }).waitFor();
}

/**
 * The P0 gate (UI spec §57, §63).
 *
 * These run against the real console: the Python server, the built bundle and
 * a real recorded run. They check the path a developer actually walks --
 * find the run, read what it did, see what it used -- rather than that a
 * component renders.
 */

test.describe("the shell", () => {
  test("prints the navigation the spec lists", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Primary" });
    for (const group of ["Build", "Run", "Quality", "Intelligence", "Project"]) {
      await expect(nav.getByText(group, { exact: true })).toBeVisible();
    }
    // Screens that are not built yet carry their phase in the label, so the
    // name is matched by prefix rather than exactly.
    for (const item of ["Overview", "Runs", "Sessions", "Replay", "Datasets", "Settings"]) {
      await expect(nav.getByRole("link", { name: new RegExp(`^${item}`) })).toBeVisible();
    }
  });

  test("opens search with the keyboard and finds a dependency", async ({ page }) => {
    await ready(page);
    await page.keyboard.press("Meta+k");
    const dialog = page.getByRole("dialog", { name: "Search" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("textbox").fill("salesforce");
    await expect(dialog.getByText("MCP servers")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  test("opens the command palette and navigates with it", async ({ page }) => {
    await ready(page);
    await page.keyboard.press("Meta+p");
    const dialog = page.getByRole("dialog", { name: "Command palette" });
    await dialog.getByRole("textbox").fill("Open Runs");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
  });

  test("every entry in the navigation leads to a screen", async ({ page }) => {
    // The spec's §4 navigation is complete: nothing in it is a dead end.
    const nav = page.getByRole("navigation", { name: "Primary" });
    await page.goto("/");
    const hrefs = await nav
      .getByRole("link")
      .evaluateAll((links) => links.map((link) => link.getAttribute("href") ?? ""));
    for (const href of hrefs) {
      await page.goto(href);
      await expect(page.locator("#main").getByRole("heading").first()).toBeVisible();
      await expect(page.getByText("is not built yet")).toHaveCount(0);
    }
  });

  test("a path that is not part of the console says so", async ({ page }) => {
    await page.goto("/nowhere");
    await expect(page.getByRole("heading", { name: "No such screen" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to the overview" })).toBeVisible();
  });
});

test.describe("the overview", () => {
  test("answers whether the system is healthy", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Healthy")).toBeVisible();
    const main = page.locator("#main");
    for (const metric of ["Success rate", "Regression", "Avg latency", "Avg cost", "Runs"]) {
      await expect(main.getByText(metric, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  });
});

test.describe("the runs page", () => {
  test("lists runs with the spec's columns and filters them", async ({ page }) => {
    await page.goto("/runs");
    for (const column of ["Time", "Agent", "User", "Model", "Latency", "Cost"]) {
      await expect(page.getByRole("columnheader", { name: column })).toBeVisible();
    }
    const all = await page.getByRole("row").count();
    expect(all).toBeGreaterThan(2); // header plus the recorded runs

    await page.getByLabel("User").selectOption("sam");
    await expect(page).toHaveURL(/user=sam/);
    await expect(page.getByRole("row")).toHaveCount(2);

    await page.getByRole("button", { name: "Clear" }).click();
    await expect(page.getByRole("row")).toHaveCount(all);
  });
});

test.describe("run detail: the signature screen", () => {
  test("shows the header, the timeline and every panel the run holds", async ({ page }) => {
    await openDemoRun(page);

    await expect(page.getByRole("heading", { level: 1 })).toContainText("run_");
    await expect(page.getByText("acme-credit").first()).toBeVisible();
    await expect(page.locator("#main").getByRole("link", { name: /^Replay/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Export" })).toBeVisible();

    // The timeline is the default tab and starts at the beginning of the run.
    await expect(page.getByText("Run started")).toBeVisible();
    await expect(page.getByText("Context built")).toBeVisible();

    // An event expands to its payload.
    await page.getByText("Tool call").first().click();
    await expect(page.locator(".event-payload").first()).toBeVisible();

    for (const tab of ["Graph", "Context", "Model", "Prompt", "Memory", "Tools", "MCP", "Skills"]) {
      await expect(page.getByRole("tab", { name: new RegExp(`^${tab}`) })).toBeEnabled();
    }
  });

  test("the context inspector shows what consumed the window", async ({ page }) => {
    await openDemoRun(page);
    await page.getByRole("tab", { name: /^Context/ }).click();

    await expect(page.getByText(/tokens used/)).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Included/ })).toBeVisible();
    await expect(page.getByText("untrusted").first()).toBeVisible();
  });

  test("the graph is drawn and its nodes are reachable", async ({ page }) => {
    await openDemoRun(page);
    await page.getByRole("tab", { name: /^Graph/ }).click();
    await expect(page.getByRole("group", { name: /Execution graph/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /plan, ok/ })).toBeVisible();
  });

  test("MCP, skills and tools report what the run actually used", async ({ page }) => {
    await openDemoRun(page);

    await page.getByRole("tab", { name: /^MCP/ }).click();
    await expect(page.getByText("salesforce").first()).toBeVisible();

    await page.getByRole("tab", { name: /^Skills/ }).click();
    await expect(page.getByText("credit-policy")).toBeVisible();

    await page.getByRole("tab", { name: /^Tools/ }).click();
    await expect(page.getByText("credit_bureau_score()")).toBeVisible();
    await expect(page.getByText("MCP · salesforce")).toBeVisible();
  });

  test("a missing run explains what to do instead of failing blankly", async ({ page }) => {
    await page.goto("/runs/run_does_not_exist");
    await expect(page.locator(".problem[role=alert]")).toContainText("Run not found");
    await expect(page.getByRole("link", { name: "Browse runs" })).toBeVisible();
  });
});

test.describe("accessibility (UI §49)", () => {
  for (const [name, path] of [
    ["overview", "/"],
    ["runs", "/runs"],
    ["sessions", "/sessions"],
  ] as const) {
    test(`${name} has no detectable violations`, async ({ page }) => {
      await page.goto(path);
      await page.waitForSelector("main");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(results.violations).toEqual([]);
    });
  }

  test("run detail has no detectable violations", async ({ page }) => {
    await openDemoRun(page);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });

  test("the execution graph is reachable and not nested interactively", async ({ page }) => {
    // Its nodes are buttons, so the drawing around them must not be an image.
    await openDemoRun(page);
    await page.getByRole("tab", { name: /^Graph/ }).click();
    await page.waitForSelector("svg");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });
});

test.describe("deep links", () => {
  test("a deep link tells assistive technology which page it is on", async ({ page }) => {
    // The bundle is prerendered at "/", so this is the regression guard for
    // hydration leaving the navigation pointing at the wrong entry (UI §49).
    await page.goto("/sessions");
    await page.waitForSelector("main");
    // `toContainText`, not `toHaveText`: a nav entry carries its label plus
    // any badge — an unbuilt screen's phase, or the marker on a screen that
    // has no data yet. The label is what identifies the current page.
    await expect(page.locator('.nav-item[aria-current="page"]')).toContainText("Sessions");

    await page.goto("/datasets");
    await page.waitForSelector("main");
    await expect(page.locator('.nav-item[aria-current="page"]')).toContainText("Datasets");
  });
});
