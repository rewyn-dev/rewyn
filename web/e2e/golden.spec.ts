import { expect, test } from "@playwright/test";

import { main, openDemoRun } from "./helpers";

/**
 * The golden scenario (UI spec §58), walked end to end.
 *
 * "Our refund agent started making incorrect decisions." The specification
 * numbers the steps a developer takes from that sentence to a regression
 * test, and says this is the experience Rewyn must nail. So it is a test.
 *
 * Steps 11 and 12 -- running the whole dataset and reading the pass/fail
 * summary -- are the regression screen, which is P2. Everything before them
 * is here.
 */
test("from a run to a regression test, without leaving the console", async ({ page }) => {
  // Step 1: open the run.
  const id = await openDemoRun(page);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(id);

  // Step 2: see the execution graph.
  await page.getByRole("tab", { name: /^Graph/ }).click();
  await expect(page.getByRole("group", { name: /Execution graph/ })).toBeVisible();

  // Steps 3 and 4: open Context and read what the run actually used.
  await page.getByRole("tab", { name: /^Context/ }).click();
  await expect(page.getByText(/tokens used/)).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Included/ })).toBeVisible();

  // Steps 5 and 6: compare with the previous successful run, and read what changed.
  await main(page)
    .getByRole("link", { name: /^Compare/ })
    .click();
  await page.getByText("previous successful run").first().click();
  await expect(page.getByRole("heading", { name: "What changed?" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Observed/ })).toBeVisible();

  // Steps 7 and 8: replay it, changing something.
  await page.goto(`/runs/${id}/replay`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Replay");
  const modelRow = page.getByRole("row").filter({ hasText: "Model" }).first();
  await modelRow.getByRole("button", { name: "recorded" }).click();
  await expect(page.getByText(/Comes from the recording, unchanged/).first()).toBeVisible();

  // Step 9: run it and compare the outputs.
  await page.getByRole("button", { name: "Run replay" }).click();
  await expect(page.getByRole("heading", { name: "Output comparison" })).toBeVisible({
    timeout: 20_000,
  });

  // Step 10: save it as a regression test.
  await page.goto(`/runs/${id}`);
  await page.getByRole("button", { name: /^Save as Test/ }).click();
  const dialog = page.getByRole("dialog", { name: "Save as test" });
  await dialog.getByLabel("Dataset").fill("golden-regression");
  await dialog.getByRole("button", { name: "Save" }).click();

  await expect(main(page).getByRole("heading", { name: "golden-regression" })).toBeVisible();
  await expect(page.getByRole("link", { name: "from run" }).first()).toBeVisible();
  await expect(page.getByText("not run yet").first()).toBeVisible();
});
