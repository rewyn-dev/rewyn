import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Live mode and approvals (UI spec §34, §35).
 *
 * The fixture's runs have all finished, so the live screen's job here is to
 * say so honestly and to stream without erroring. The mechanics of watching a
 * run as it is being written are covered where they can be driven directly,
 * in `tests/ui/test_live.py`.
 */

test.describe("live mode (UI §34)", () => {
  test("connects, and says plainly when nothing is running", async ({ page }) => {
    await page.goto("/live");
    await expect(page.getByRole("heading", { name: "Live", level: 1 })).toBeVisible();
    await expect(page.getByText("streaming")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "Nothing is running" })).toBeVisible();
  });

  test("shows the approvals queue and what it is for", async ({ page }) => {
    await page.goto("/live");
    await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
    await expect(page.getByText(/Nothing is waiting for a person/)).toBeVisible();
    await expect(page.getByText("InboxHandler()")).toBeVisible();
  });

  test("has no detectable accessibility violations", async ({ page }) => {
    await page.goto("/live");
    await page.waitForSelector("main");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });
});
