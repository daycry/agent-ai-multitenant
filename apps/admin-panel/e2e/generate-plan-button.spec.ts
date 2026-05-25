import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the contextual "Generar Plan" button (Plan 03 task_03_13).
 *
 * The button only appears when the LAST agent message in the chat
 * carries an attachment of the shape
 *   {"kind": "planning_directive", "intent": "finish_planning"}.
 *
 * Clicking it POSTs `/projects/{id}/plans` with the conversation id
 * and navigates to the new plan's detail page.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000dd";
const NEW_PLAN_ID = "0000bbbb-0000-0000-0000-000000000001";

function conversation() {
  return {
    id: CONVERSATION_ID,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    project_id: PROJECT_ID,
    title: "Planning Inventory API",
    current_mode: "planning",
    custom_mode_name: null,
    related_plan_id: null,
    created_by: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: "2026-05-24T10:00:00Z",
  };
}

function agentMessage(opts: { intent?: "finish_planning" | "speak_alone" }) {
  const attachments = opts.intent ? [{ kind: "planning_directive", intent: opts.intent }] : [];
  return {
    id: "44444444-4444-4444-4444-444444444444",
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    conversation_id: CONVERSATION_ID,
    author_kind: "agent",
    author_user_id: null,
    author_agent_id: "aaaa0000-0000-0000-0000-00000000aaaa",
    content: "Equipo: tenemos el plan listo.",
    mode: "planning",
    attachments,
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-05-24T10:01:00Z",
  };
}

interface PostCapture {
  calls: number;
  lastBody: { conversation_id?: string };
}

async function setup(
  page: Page,
  opts: { intent?: "finish_planning" | "speak_alone" } = {},
): Promise<PostCapture> {
  const capture: PostCapture = { calls: 0, lastBody: {} };
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`**/projects/${PROJECT_ID}/conversations`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([conversation()]),
    });
  });
  await page.route(`**/conversations/${CONVERSATION_ID}/messages*`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([agentMessage(opts)]),
    });
  });
  await page.route(`**/projects/${PROJECT_ID}/plans`, (route) => {
    if (route.request().method() !== "POST") return route.continue();
    capture.calls += 1;
    capture.lastBody = JSON.parse(route.request().postData() ?? "{}");
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: NEW_PLAN_ID, project_id: PROJECT_ID }),
    });
  });
  // Stub the destination page so the navigation after POST doesn't
  // 404 the test (the real plan detail page lands in task_03_18).
  await page.route(`**/admin/projects/${PROJECT_ID}/plans/${NEW_PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body><h1 data-testid='stub-plan-detail'>Plan</h1></body></html>",
    }),
  );
  return capture;
}

test("Generar Plan is hidden when the PM has not signalled finish_planning", async ({ page }) => {
  await setup(page, { intent: "speak_alone" });
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });
  // Wait for messages to render before asserting the button is absent.
  await expect(page.getByTestId("chat-message-agent")).toBeVisible();
  await expect(page.getByTestId("generate-plan-cta")).toHaveCount(0);
});

test("Generar Plan appears when the latest agent message intent=finish_planning", async ({
  page,
}) => {
  await setup(page, { intent: "finish_planning" });
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });
  const cta = page.getByTestId("generate-plan-cta");
  await expect(cta).toBeVisible();
  await expect(page.getByTestId("generate-plan-button")).toContainText("Generar Plan");
});

test("clicking Generar Plan POSTs the conversation id and navigates to the new plan", async ({
  page,
}) => {
  const capture = await setup(page, { intent: "finish_planning" });
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByTestId("generate-plan-button").click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.conversation_id).toBe(CONVERSATION_ID);

  // Navigation completed to the (stubbed) plan detail page.
  await expect(page).toHaveURL(new RegExp(`/admin/projects/${PROJECT_ID}/plans/${NEW_PLAN_ID}$`));
});
