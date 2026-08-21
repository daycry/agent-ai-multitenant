import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the chat mode selector (Plan 03 task_03_05).
 *
 * The chat page exposes a segmented control (Planning / Discusión /
 * Ejecución) in the header. Selecting a different mode does a PUT
 * /conversations/{id} with `current_mode`, and the active state moves
 * to the clicked pill once the request returns.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa";

function conversation(mode: string) {
  return {
    id: CONVERSATION_ID,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    project_id: PROJECT_ID,
    title: "Chat de planning",
    current_mode: mode,
    custom_mode_name: null,
    related_plan_id: null,
    created_by: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: "2026-05-24T10:00:00Z",
  };
}

interface PutCapture {
  calls: number;
  lastBody: { current_mode?: string };
}

async function setup(page: Page): Promise<PutCapture> {
  const capture: PutCapture = { calls: 0, lastBody: {} };
  let currentMode = "planning";

  await seedSession(page);

  await page.route(`**/projects/${PROJECT_ID}/conversations`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([conversation(currentMode)]),
    });
  });

  await page.route(`**/conversations/${CONVERSATION_ID}`, (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    capture.calls += 1;
    capture.lastBody = JSON.parse(route.request().postData() ?? "{}");
    if (capture.lastBody.current_mode) {
      currentMode = capture.lastBody.current_mode;
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(conversation(currentMode)),
    });
  });

  return capture;
}

test("chat page shows the mode selector with planning active by default", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  const selector = page.getByTestId("chat-mode-selector");
  await expect(selector).toBeVisible();

  // All three built-in pills present.
  await expect(page.getByTestId("chat-mode-planning")).toBeVisible();
  await expect(page.getByTestId("chat-mode-discussion")).toBeVisible();
  await expect(page.getByTestId("chat-mode-execution")).toBeVisible();

  // Planning is active to start with.
  await expect(page.getByTestId("chat-mode-planning")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("chat-current-mode")).toHaveText("planning");
});

test("clicking a mode pill PUTs the new mode and updates the active state", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("chat-mode-discussion").click();

  // The button-level active flag flips immediately (optimistic) but
  // the displayed mode also has to match once the PUT settles.
  await expect(page.getByTestId("chat-mode-discussion")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("chat-current-mode")).toHaveText("discussion");
  await expect(page.getByTestId("chat-mode-planning")).toHaveAttribute("data-active", "false");

  expect(capture.calls).toBe(1);
  expect(capture.lastBody.current_mode).toBe("discussion");
});

test("clicking the already-active mode does not PUT", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  // Wait until the planning pill renders active before clicking it again.
  await expect(page.getByTestId("chat-mode-planning")).toHaveAttribute("data-active", "true");
  await page.getByTestId("chat-mode-planning").click();
  // Give the click a moment to take effect; no PUT should fire.
  await page.waitForTimeout(250);

  expect(capture.calls).toBe(0);
});
