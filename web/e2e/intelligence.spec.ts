import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Dependencies, drift, cost, releases, experiments and notifications
 * (UI spec §28, §31-§33, §40, §43).
 *
 * These screens claim causes and safety, so the tests check the claims are
 * hedged where the evidence is: unverified is not ready, and a drift with no
 * recorded cause says so.
 */

test.describe("dependencies (UI §31)", () => {
  test("draws the map and makes every node clickable", async ({ page }) => {
    await page.goto("/dependencies?agent=acme-credit");
    await expect(page.getByRole("group", { name: /Dependency map for acme-credit/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "mcp_server salesforce" })).toBeVisible();

    await page.getByRole("button", { name: "skill credit-policy" }).click();
    await expect(page).toHaveURL(/\/skills\/credit-policy/);
  });

  test("starts from an agent rather than guessing", async ({ page }) => {
    await page.goto("/dependencies");
    await expect(page.getByRole("heading", { name: "Pick an agent" })).toBeVisible();
  });
});

test.describe("drift (UI §32)", () => {
  test("compares two windows and names what changed, or says it cannot", async ({ page }) => {
    await page.goto("/drift?agent=acme-credit");
    await expect(page.getByText("Expected success")).toBeVisible();
    await expect(page.getByText("Current", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Possible causes" })).toBeVisible();
  });

  test("says when there is not enough history rather than inventing a trend", async ({ page }) => {
    await page.goto("/drift?agent=onboarding-bot");
    await expect(page.locator(".problem[role=alert]")).toContainText("Not enough history");
  });
});

test.describe("cost (UI §33)", () => {
  test("answers in cost per successful task and groups by every dimension", async ({ page }) => {
    await page.goto("/cost");
    await expect(page.getByText("Per successful task").first()).toBeVisible();
    for (const category of ["Model", "Tools", "Embedding", "Retrieval", "Sandbox"]) {
      await expect(page.getByText(category, { exact: true }).first()).toBeVisible();
    }

    await page.getByLabel("Group by").selectOption("model");
    await expect(page.getByRole("columnheader", { name: "model" })).toBeVisible();
    await page.getByLabel("Group by").selectOption("time");
    await expect(page.getByRole("columnheader", { name: "time" })).toBeVisible();
  });

  test("is reachable from the number that raises the question", async ({ page }) => {
    await page.goto("/");
    await page.getByText("Avg cost").click();
    await expect(page.getByRole("heading", { name: "Cost", level: 1 })).toBeVisible();
  });
});

test.describe("releases (UI §43)", () => {
  test("calls a version unverified rather than safe", async ({ page }) => {
    await page.goto("/releases");
    await expect(page.getByText("UNVERIFIED").first()).toBeVisible();
    await expect(page.getByText(/No regression report for this version yet/).first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Promote to production" }).first(),
    ).toBeDisabled();
  });

  test("refuses to promote an unverified version even if the button is bypassed", async ({
    page,
  }) => {
    // UI §54: authorization is enforced at the API, never by a disabled
    // button. This asks the server directly, the way anything but a browser
    // would.
    await page.goto("/releases");
    const refusal = await page.evaluate(async () => {
      const listed = await (await fetch("/console/v1/releases")).json();
      const response = await fetch(
        `/console/v1/releases/${encodeURIComponent(listed[0].id)}/promote`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ environment: "production" }),
        },
      );
      return { status: response.status, body: await response.json() };
    });
    expect(refusal.status).toBe(409);
    expect(refusal.body.detail.detail).toContain("not the same as ready");
  });
});

test.describe("notifications (UI §40)", () => {
  test("are reachable from the shell and say what happened", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: /Notifications:/ }).click();
    await expect(page.getByRole("heading", { name: "Notifications", level: 1 })).toBeVisible();
    await expect(page.getByText("Meaningful AI events, one per cause.")).toBeVisible();
  });
});

test.describe("accessibility of the P3 screens (UI §49)", () => {
  for (const path of ["/dependencies?agent=acme-credit", "/cost", "/releases", "/notifications"]) {
    test(`${path} has no detectable violations`, async ({ page }) => {
      await page.goto(path);
      await page.waitForSelector("main");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(results.violations).toEqual([]);
    });
  }
});
