import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the chat mode-change side-effects (Plan 03 task_03_07).
 *
 * When the operator flips the chat mode in the header selector:
 *   - the conversation's current_mode persists (PUT /conversations/{id}),
 *   - the backend posts a `system` "modo cambiado" message,
 *   - the chat feed renders that banner immediately on refetch,
 *   - the prior conversation context (older messages) stays visible,
 *   - the active mode pill flips to the new mode.
 *
 * The Fase A REST contract is the same for live traffic; here we mock
 * it server-side so the test runs without an api-server, and verify
 * the *visible* behaviour the operator observes.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa";

function conversation(mode: string) {
  return {
    id: CONVERSATION_ID,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    project_id: PROJECT_ID,
    title: "Inventory API",
    current_mode: mode,
    custom_mode_name: null,
    related_plan_id: null,
    created_by: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: "2026-05-24T10:00:00Z",
  };
}

function userMessage(id: string, content: string, mode: string) {
  return {
    id,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    conversation_id: CONVERSATION_ID,
    author_kind: "user",
    author_user_id: "uuuu0000-0000-0000-0000-000000000001",
    author_agent_id: null,
    content,
    mode,
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-05-24T10:00:00Z",
  };
}

function systemBanner(id: string, content: string, mode: string) {
  return {
    id,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    conversation_id: CONVERSATION_ID,
    author_kind: "system",
    author_user_id: null,
    author_agent_id: null,
    content,
    mode,
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-05-24T10:01:00Z",
  };
}

async function setup(page: Page): Promise<void> {
  let currentMode = "planning";
  // Two original messages — the test will check these survive the
  // mode switch.
  const original = [
    userMessage("11111111-1111-1111-1111-111111111111", "Hola equipo", "planning"),
    userMessage("11111111-1111-1111-1111-111111111112", "Empecemos a planear", "planning"),
  ];

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

  await page.route(`**/projects/${PROJECT_ID}/conversations`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([conversation(currentMode)]),
    });
  });

  // PUT changes the active mode AND grows the feed by one banner.
  await page.route(`**/conversations/${CONVERSATION_ID}`, (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    const body = JSON.parse(route.request().postData() ?? "{}");
    if (body.current_mode) {
      const oldMode = currentMode;
      currentMode = body.current_mode;
      original.push(
        systemBanner(
          "22222222-2222-2222-2222-222222222222",
          `Modo cambiado: ${oldMode} -> ${currentMode}`,
          currentMode,
        ),
      );
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(conversation(currentMode)),
    });
  });

  // Messages endpoint returns whatever has been queued so far.
  await page.route(`**/conversations/${CONVERSATION_ID}/messages*`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(original),
    });
  });
}

test("changing the mode posts the system banner and preserves the prior feed", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  // Sanity: the two pre-existing user messages render before any change.
  await expect(page.getByText("Hola equipo")).toBeVisible();
  await expect(page.getByText("Empecemos a planear")).toBeVisible();
  await expect(page.getByTestId("chat-system-banner")).toHaveCount(0);

  // Flip mode to discussion.
  await page.getByTestId("chat-mode-discussion").click();

  // The selector reflects the new mode.
  await expect(page.getByTestId("chat-mode-discussion")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("chat-current-mode")).toHaveText("discussion");

  // The new system banner appears AND the previous messages survive
  // (context preserved). The banner mentions both modes.
  const banner = page.getByTestId("chat-system-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("planning");
  await expect(banner).toContainText("discussion");
  await expect(page.getByText("Hola equipo")).toBeVisible();
  await expect(page.getByText("Empecemos a planear")).toBeVisible();
});
