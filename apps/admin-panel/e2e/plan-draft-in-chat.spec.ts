import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for structured plan drafts in the chat (Plan 03 task_03_11).
 *
 * Agent messages can carry markdown — headings, tables (GFM), and
 * lists — and the chat feed must render them as actual HTML elements
 * instead of dumping the raw markdown. User messages, by contrast,
 * stay verbatim (no markdown processing).
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000bb";

const PLAN_DRAFT_CONTENT = [
  "## Borrador del plan: Inventory API",
  "",
  "El equipo propone **3 fases** para entregar el MVP.",
  "",
  "### Fases",
  "1. Diseño del esquema y de la auth",
  "2. Implementación de los handlers",
  "3. QA y despliegue",
  "",
  "### Tareas iniciales",
  "| Tarea | Rol | Complejidad |",
  "|---|---|---|",
  "| Modelar entidades | architect | m |",
  "| Definir endpoints | backend_dev | m |",
  "| Diseñar test plan | qa | s |",
].join("\n");

function conversation() {
  return {
    id: CONVERSATION_ID,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    project_id: PROJECT_ID,
    title: "Planning del Inventory API",
    current_mode: "planning",
    custom_mode_name: null,
    related_plan_id: null,
    created_by: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: "2026-05-24T10:00:00Z",
  };
}

function userMessage() {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    conversation_id: CONVERSATION_ID,
    author_kind: "user",
    author_user_id: "uuuu0000-0000-0000-0000-000000000001",
    author_agent_id: null,
    content: "Necesito un plan para una API de inventario con auth.",
    mode: "planning",
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-05-24T10:00:00Z",
  };
}

function agentDraftMessage() {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    conversation_id: CONVERSATION_ID,
    author_kind: "agent",
    author_user_id: null,
    author_agent_id: "aaaa0000-0000-0000-0000-00000000aaaa",
    content: PLAN_DRAFT_CONTENT,
    mode: "planning",
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-05-24T10:01:00Z",
  };
}

async function setup(page: Page): Promise<void> {
  await seedSession(page);
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
      body: JSON.stringify([userMessage(), agentDraftMessage()]),
    });
  });
}

test("agent plan draft renders headings, lists and tables", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  // The agent message exists with the plan-draft wrapper.
  const draft = page.getByTestId("plan-draft");
  await expect(draft).toBeVisible();

  // Headings rendered (not raw "## Borrador..."). The big top heading
  // is level 2; the sub-section "Fases" is level 3.
  const headings = page.getByTestId("plan-draft-heading");
  await expect(headings.first()).toContainText("Borrador del plan: Inventory API");
  await expect(headings.first()).toHaveAttribute("data-level", "2");
  await expect(headings.first()).not.toContainText("##");
  const fases = headings.filter({ hasText: "Fases" }).first();
  await expect(fases).toBeVisible();
  await expect(fases).toHaveAttribute("data-level", "3");

  // Bold rendered as <strong>.
  const bold = draft.locator("strong").first();
  await expect(bold).toContainText("3 fases");

  // Ordered list with 3 items.
  const ol = page.getByTestId("plan-draft-ol");
  await expect(ol).toBeVisible();
  await expect(ol.locator("li")).toHaveCount(3);
  await expect(ol.locator("li").nth(0)).toContainText("Diseño del esquema");

  // Table with headers + 3 data rows.
  const table = page.getByTestId("plan-draft-table");
  await expect(table).toBeVisible();
  await expect(table.locator("thead th")).toHaveCount(3);
  await expect(table.locator("thead th").nth(0)).toContainText("Tarea");
  await expect(table.locator("tbody tr")).toHaveCount(3);
  await expect(table.locator("tbody tr").nth(0).locator("td").nth(1)).toContainText("architect");

  // Raw "|" or "**" pipes must not leak into the rendered output.
  await expect(draft).not.toContainText("|---|");
  await expect(draft).not.toContainText("**3 fases**");
});

test("user messages stay verbatim and are not run through the markdown renderer", async ({
  page,
}) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  const userMsg = page.getByTestId("chat-message-user");
  await expect(userMsg).toContainText("Necesito un plan para una API de inventario con auth.");
  // The user bubble is not wrapped in a plan-draft container.
  await expect(userMsg.getByTestId("plan-draft")).toHaveCount(0);
});
