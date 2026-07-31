import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the approval UI (task_02_26).
 *
 * Approve / Reject buttons + an optional reason. Resolving a request
 * POSTs `/approvals/{id}/resolve` and drops the card from the feed.
 */

const REQUEST = {
  id: "aa000000-0000-0000-0000-000000000002",
  execution_id: "ee000000-0000-0000-0000-000000000001",
  task_id: "22222222-2222-2222-2222-222222222222",
  project_id: "33333333-3333-3333-3333-333333333333",
  category: "production_deploy",
  action: { tool: "shell_exec", args: { command: "deploy.sh" } },
  status: "pending",
  requested_at: "2026-05-22T10:00:00Z",
};

interface ResolveCapture {
  calls: number;
  lastBody: { approved?: boolean; reason?: string | null };
}

async function setup(page: Page): Promise<ResolveCapture> {
  const capture: ResolveCapture = { calls: 0, lastBody: {} };
  let resolved = false;

  await seedSession(page);

  // The feed empties once the request is resolved.
  await page.route("http://localhost:8001/approvals", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(resolved ? [] : [REQUEST]),
    }),
  );

  await page.route(`http://localhost:8001/approvals/${REQUEST.id}/resolve`, (route) => {
    if (route.request().method() !== "POST") return route.continue();
    capture.calls += 1;
    capture.lastBody = JSON.parse(route.request().postData() ?? "{}");
    resolved = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...REQUEST, status: "approved" }),
    });
  });

  return capture;
}

test("approving a request POSTs the decision and clears the card", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toBeVisible();
  await page.getByTestId(`approve-${REQUEST.id}`).click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.approved).toBe(true);

  // The resolved request drops off the feed.
  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toHaveCount(0);
  await expect(page.getByTestId("approvals-empty")).toBeVisible();
});

test("rejecting a request sends the optional reason", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`reason-${REQUEST.id}`).fill("deploy window closed");
  await page.getByTestId(`reject-${REQUEST.id}`).click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.approved).toBe(false);
  expect(capture.lastBody.reason).toBe("deploy window closed");
});
