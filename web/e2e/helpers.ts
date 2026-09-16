import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * Open the newest run of the demo agent.
 *
 * Everything the console does is recorded, so the fixture's store also holds
 * evaluation runs and any replay these tests produce. "A run" in these tests
 * means an execution of the demo agent, so it is selected by name rather than
 * by being at the top of the table.
 */
export async function openDemoRun(page: Page): Promise<string> {
  if (!page.url().startsWith("http")) await page.goto("/runs");
  const id = await page.evaluate(async () => {
    const response = await fetch("/console/v1/runs?limit=100");
    const body = (await response.json()) as {
      runs: { id: string; agent: string | null; tags: string[] }[];
    };
    return (
      body.runs.find((run) => run.agent === "acme-credit" && !run.tags.includes("replay"))?.id ?? ""
    );
  });
  expect(id).not.toBe("");
  await page.goto(`/runs/${id}`);
  await page.waitForSelector(".timeline");
  return id;
}

/** The screen, without the sidebar: some actions share a name with a nav entry. */
export function main(page: Page) {
  return page.locator("#main");
}
