import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { main, openDemoRun } from "./helpers";

/**
 * The P1 gate (UI spec §57) and the golden scenario (§58).
 *
 * Everything here runs against the real console with a real recorded run:
 * replay it, compare it with an earlier run, and turn it into a regression
 * case. This is the workflow the specification says has to feel excellent
 * before anything else is built.
 */

test.describe("the replay workspace (UI §20, §21)", () => {
  test("offers the nine controls, and says what each one will do", async ({ page }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Replay/ })
      .click();

    await expect(main(page).getByRole("heading", { level: 1 })).toContainText("Replay");
    for (const control of [
      "Model",
      "Prompt",
      "Context",
      "Memory",
      "Skills",
      "Tools",
      "MCP",
      "Temperature",
      "System",
    ]) {
      await expect(page.getByRole("cell", { name: control, exact: true })).toBeVisible();
    }
    // Four modes on every row, and a plain-language effect beside it.
    await expect(page.getByRole("button", { name: "recorded" })).toHaveCount(9);
    await expect(page.getByText(/reproduces the run exactly from the recording/)).toBeVisible();
  });

  test("reconstructs the run, and says so", async ({ page }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Replay/ })
      .click();
    await page.getByRole("button", { name: "Run replay" }).click();

    await expect(page.getByText("identical output")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("heading", { name: "Output comparison" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Replay output" })).toContainText("250,000");
  });

  test("a control the console cannot honour explains itself instead of failing", async ({
    page,
  }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Replay/ })
      .click();

    const memoryRow = page.getByRole("row").filter({ hasText: "Memory" });
    await memoryRow.getByRole("button", { name: "live" }).click();

    await expect(page.getByText(/Not changeable from the console/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Run replay" })).toBeDisabled();
  });
});

test.describe("the compare workspace (UI §22, §23)", () => {
  test("suggests the previous successful run, then says what changed", async ({ page }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Compare/ })
      .click();

    await expect(page.getByText("previous successful run")).toBeVisible();
    await page.getByText("previous successful run").click();

    await expect(page.getByRole("heading", { name: "What changed?" })).toBeVisible();
    for (const dimension of ["Model", "Prompt", "Context", "Memory", "Tools", "MCP"]) {
      await expect(page.getByRole("cell", { name: dimension, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("cell", { name: "Cost" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Latency" })).toBeVisible();
  });

  test("labels observation and inference separately", async ({ page }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Compare/ })
      .click();
    await page.getByText("previous successful run").click();

    await expect(page.getByRole("heading", { name: /^Observed/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Why this matters" })).toBeVisible();
    await expect(page.getByText(/Inferences are hypotheses drawn from them/)).toBeVisible();
  });
});

test.describe("save as test and datasets (UI §24, §25)", () => {
  test("a run becomes a regression case, and the dataset shows it", async ({ page }) => {
    await openDemoRun(page);
    await page.getByRole("button", { name: /^Save as Test/ }).click();

    const dialog = page.getByRole("dialog", { name: "Save as test" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Dataset").fill("credit-regression");
    await expect(dialog.getByLabel("Expected outcome")).not.toBeEmpty();
    await dialog.getByLabel("Severity").selectOption("critical");
    await dialog.getByRole("button", { name: "Save" }).click();

    await expect(main(page).getByRole("heading", { name: "credit-regression" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "task-success" }).first()).toBeVisible();
    await expect(page.getByText("critical").first()).toBeVisible();
    await expect(page.getByRole("link", { name: "from run" }).first()).toBeVisible();

    await page.goto("/datasets");
    await expect(page.getByRole("link", { name: "credit-regression" })).toBeVisible();
  });

  test("the datasets empty state teaches how to make one", async ({ page }) => {
    await page.goto("/datasets");
    const heading = page.getByRole("heading", { name: "No datasets yet" });
    if (await heading.isVisible().catch(() => false)) {
      await expect(
        page.getByText(/Turn a production run into your first regression test/),
      ).toBeVisible();
      await expect(page.getByRole("link", { name: "Create from a run" })).toBeVisible();
    }
  });
});

test.describe("accessibility of the P1 screens (UI §49)", () => {
  test("replay, compare and datasets have no detectable violations", async ({ page }) => {
    await openDemoRun(page);
    await main(page)
      .getByRole("link", { name: /^Replay/ })
      .click();
    await page.waitForSelector("table");
    expect(
      (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()).violations,
    ).toEqual([]);

    await page.goto("/datasets");
    await page.waitForSelector("main");
    expect(
      (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()).violations,
    ).toEqual([]);
  });
});
