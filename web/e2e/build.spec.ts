import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * The BUILD pages, agent pages and quality screens (UI spec §26-§30).
 *
 * All of it is derived from what runs recorded, so these tests only need the
 * fixture's runs to exist -- nothing is configured anywhere.
 */

test.describe("the BUILD pages (UI §4, §29)", () => {
  test("agents are listed with what their runs used", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.getByRole("link", { name: "acme-credit" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Version" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Success" })).toBeVisible();
  });

  test("an agent page carries the tabs the spec lists", async ({ page }) => {
    await page.goto("/agents/acme-credit");
    for (const tab of [
      "overview",
      "runs",
      "configuration",
      "dependencies",
      "evaluations",
      "versions",
    ]) {
      await expect(page.getByRole("tab", { name: tab })).toBeVisible();
    }
    await expect(page.getByText("Runs", { exact: true }).first()).toBeVisible();

    await page.getByRole("tab", { name: "configuration" }).click();
    await expect(page.getByRole("cell", { name: "credit-policy" })).toBeVisible();

    await page.getByRole("tab", { name: "versions" }).click();
    await expect(page.getByRole("cell", { name: "v1", exact: true })).toBeVisible();
    await expect(page.getByText(/One version has run so far/)).toBeVisible();
  });

  test("every BUILD page lists what has actually run", async ({ page }) => {
    for (const [path, expected] of [
      ["/models", "fake:research-1"],
      ["/skills", "credit-policy"],
      ["/tools", "set_credit_limit"],
      ["/mcp", "salesforce"],
      ["/memory", "account-memory"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("link", { name: expected })).toBeVisible();
    }
  });

  test("a BUILD entry says which agents used it", async ({ page }) => {
    await page.goto("/skills");
    await page.getByRole("link", { name: "credit-policy" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("credit-policy");
    await expect(page.getByRole("heading", { name: "Used by" })).toBeVisible();
    await expect(page.locator("#main").getByRole("link", { name: "acme-credit" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  });

  test("an empty BUILD page teaches instead of showing nothing", async ({ page }) => {
    await page.goto("/prompts");
    const empty = page.getByRole("heading", { name: "No prompts yet" });
    if (await empty.isVisible().catch(() => false)) {
      await expect(
        page.getByText(/A run's exact prompt is always on its Prompt tab/),
      ).toBeVisible();
    }
  });
});

test.describe("quality (UI §26, §27)", () => {
  test("evaluations show a distribution, not a single number", async ({ page }) => {
    await page.goto("/evaluations");
    await expect(page.getByText("Evaluators", { exact: true })).toBeVisible();
    await expect(page.getByText("Runs scored", { exact: true })).toBeVisible();

    // The fixture scores one run with two evaluators, at 0.92 and 0.55.
    await expect(page.getByText("task_success")).toBeVisible();
    await expect(page.getByText("mean 0.92")).toBeVisible();
    await expect(page.getByText("groundedness")).toBeVisible();
    await expect(page.getByText("mean 0.55")).toBeVisible();

    // A distribution, not a number: the histogram describes itself.
    await expect(page.getByRole("img", { name: /0\.9-1: 1/ })).toBeVisible();
  });

  test("regression composes the command it cannot run itself", async ({ page }) => {
    await page.goto("/regression");
    await expect(page.getByText("New experiment")).toBeVisible();
    await page.getByLabel("Dataset").fill("credit-regression");
    await page.getByLabel("Candidate").fill("app.agents:credit");
    await page.getByRole("button", { name: "Run", exact: true }).click();

    await expect(
      page.getByRole("region", { name: "Command to run this experiment" }),
    ).toContainText("rewyn test credit-regression --target app.agents:credit --baseline latest");
    await expect(page.getByText(/runs where your code is/)).toBeVisible();
  });

  test("the regression list teaches when it is empty", async ({ page }) => {
    await page.goto("/regression");
    const empty = page.getByRole("heading", { name: "No regression runs yet" });
    if (await empty.isVisible().catch(() => false)) {
      await expect(page.getByRole("link", { name: "Datasets" }).last()).toBeVisible();
    }
  });
});

test.describe("accessibility of the P2 screens (UI §49)", () => {
  for (const path of ["/agents", "/models", "/evaluations", "/regression"]) {
    test(`${path} has no detectable violations`, async ({ page }) => {
      await page.goto(path);
      await page.waitForSelector("main");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(results.violations).toEqual([]);
    });
  }
});
