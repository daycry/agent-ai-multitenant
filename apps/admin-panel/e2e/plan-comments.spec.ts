import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for inline plan comments (Plan 03 task_03_21).
 *
 * The plan detail page exposes a comments card with a list of
 * existing comments and a form to add a new one. The form can target
 * the whole plan OR a specific task id; the POSTed body matches the
 * shape expected by `POST /plans/{id}/comments`.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-00000000c0c0";

const PLAN_FIXTURE = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "Plan con comentarios",
  description: null,
  status: "pending_approval",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-05-24T10:00:00Z",
  updated_at: "2026-05-24T10:00:00Z",
  specification: {
    tasks: [
      { id: "t1", title: "Modelar", depends_on: [] },
      { id: "t2", title: "Implementar", depends_on: ["t1"] },
    ],
  },
};

interface PostCapture {
  calls: number;
  lastBody: {
    target_kind?: string;
    target_ref?: string | null;
    content?: string;
  };
}

async function setup(page: Page): Promise<PostCapture> {
  const capture: PostCapture = { calls: 0, lastBody: {} };
  const persisted: Array<Record<string, unknown>> = [];

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PLAN_FIXTURE),
    });
  });
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/comments`, (route) => {
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
        id: `cccc0000-0000-0000-0000-${String(capture.calls).padStart(12, "0")}`,
        tenant_id: "tttt0000-0000-0000-0000-000000000001",
        plan_id: PLAN_ID,
        target_kind: body.target_kind,
        target_ref: body.target_ref ?? null,
        author_user_id: "uuuu0000-0000-0000-0000-000000000001",
        content: body.content,
        created_at: "2026-05-24T10:01:00Z",
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

test("empty state shows when no comments exist", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-comments-empty")).toBeVisible();
});

test("posting a plan-scoped comment renders it in the list", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plan-comment-content").fill("Falta detalle de seguridad.");
  await page.getByTestId("plan-comment-submit").click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.target_kind).toBe("plan");
  // For target_kind=plan the body may omit target_ref entirely.
  expect(capture.lastBody.target_ref ?? null).toBeNull();
  expect(capture.lastBody.content).toBe("Falta detalle de seguridad.");

  const list = page.getByTestId("plan-comments-list");
  await expect(list).toContainText("Falta detalle de seguridad.");
  await expect(list).toContainText("Sobre el plan");
});

test("posting a task-scoped comment POSTs the right target_ref", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  // Switch the target dropdown to a specific task.
  await page.getByTestId("plan-comment-target-kind").selectOption("task");
  await page.getByTestId("plan-comment-target-ref").selectOption("t2");
  await page.getByTestId("plan-comment-content").fill("¿De qué proveedor?");
  await page.getByTestId("plan-comment-submit").click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.target_kind).toBe("task");
  expect(capture.lastBody.target_ref).toBe("t2");
  expect(capture.lastBody.content).toBe("¿De qué proveedor?");

  const list = page.getByTestId("plan-comments-list");
  await expect(list).toContainText("¿De qué proveedor?");
  await expect(list).toContainText("Sobre tarea");
  await expect(list).toContainText("t2");
});

test("submit is disabled when the textarea is empty", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-comment-submit")).toBeDisabled();
  await page.getByTestId("plan-comment-content").fill("ok");
  await expect(page.getByTestId("plan-comment-submit")).toBeEnabled();
});
