import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for @-mentions in the chat composer (Plan 03 task_03_12).
 *
 * Typing `@` opens an autocomplete with the project's team roles; picking
 * one inserts `@<role> ` into the input. Sending the message POSTs
 * the literal text containing the mention; the chat feed shows it.
 *
 * task_wf_43: la lista sale de `GET /projects/{id}/planning-roles` (el equipo
 * REAL), no del enum entero, así que el mock tiene que servirla.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const CONVERSATION_ID = "00000000-0000-0000-0000-0000000000cc";

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

interface PostCapture {
  lastBody: { author_kind?: string; content?: string };
  calls: number;
}

async function setup(page: Page): Promise<PostCapture> {
  const capture: PostCapture = { lastBody: {}, calls: 0 };
  const persisted: Array<Record<string, unknown>> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

  await page.route(`**/projects/${PROJECT_ID}/planning-roles`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        roles: ["architect", "backend_dev", "project_manager", "qa"],
      }),
    }),
  );

  await page.route(`**/projects/${PROJECT_ID}/conversations`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([conversation()]),
    });
  });

  await page.route(`**/conversations/${CONVERSATION_ID}/messages*`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(persisted),
      });
    }
    if (route.request().method() === "POST") {
      capture.calls += 1;
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.lastBody = body;
      const stored = {
        id: `0000aaaa-0000-0000-0000-${String(capture.calls).padStart(12, "0")}`,
        tenant_id: "tttt0000-0000-0000-0000-000000000001",
        conversation_id: CONVERSATION_ID,
        author_kind: body.author_kind ?? "user",
        author_user_id: "uuuu0000-0000-0000-0000-000000000001",
        author_agent_id: null,
        content: body.content ?? "",
        mode: "planning",
        attachments: [],
        related_plan_id: null,
        is_summary: false,
        created_at: "2026-05-24T10:00:00Z",
      };
      persisted.push(stored);
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(stored),
      });
    }
    return route.continue();
  });

  return capture;
}

test("typing '@' opens the role autocomplete and picking one inserts the mention", async ({
  page,
}) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  const input = page.getByTestId("chat-input");
  await input.fill("hola equipo, @");

  const suggestions = page.getByTestId("mention-suggestions");
  await expect(suggestions).toBeVisible();

  // Todos los roles DEL EQUIPO aparecen con la consulta vacía.
  await expect(page.getByTestId("mention-suggestion-architect")).toBeVisible();
  await expect(page.getByTestId("mention-suggestion-qa")).toBeVisible();
  await expect(page.getByTestId("mention-suggestion-backend_dev")).toBeVisible();
  // Y uno que el equipo NO tiene no se ofrece: mencionarlo daría un turno vacío.
  await expect(page.getByTestId("mention-suggestion-security")).toHaveCount(0);

  // Pick architect — `@architect ` lands in the buffer and the
  // dropdown closes.
  await page.getByTestId("mention-suggestion-architect").click();
  await expect(input).toHaveValue("hola equipo, @architect ");
  await expect(page.getByTestId("mention-suggestions")).toHaveCount(0);
});

test("autocomplete filters by prefix as the user types", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  const input = page.getByTestId("chat-input");
  await input.fill("@back");

  // Only roles starting with "back" survive.
  await expect(page.getByTestId("mention-suggestion-backend_dev")).toBeVisible();
  await expect(page.getByTestId("mention-suggestion-architect")).toHaveCount(0);
  await expect(page.getByTestId("mention-suggestion-qa")).toHaveCount(0);
});

test("sending a message with a mention POSTs the literal text including @role", async ({
  page,
}) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/chat`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("chat-input").fill("@architect ¿podemos revisar el esquema?");
  await page.getByTestId("chat-send").click();

  // POST hit the API with the full text.
  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.author_kind).toBe("user");
  expect(capture.lastBody.content).toBe("@architect ¿podemos revisar el esquema?");

  // The feed renders the new user message verbatim (no markdown
  // mangling for user turns — including the @mention).
  const userMsg = page.getByTestId("chat-message-user");
  await expect(userMsg).toContainText("@architect ¿podemos revisar el esquema?");

  // Composer was cleared after send.
  await expect(page.getByTestId("chat-input")).toHaveValue("");
});
