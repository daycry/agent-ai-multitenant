import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the human-task delivery form — "Entregar tarea" (Plan 16 task_16_09).
 *
 * From the personal inbox (/admin/inbox), the assignee marks an ACCEPTED task
 * complete via a delivery modal that collects:
 *   - an output / result textarea
 *   - attachments (URLs / files / screenshots — references)
 *   - an OPTIONAL hours-worked field
 * and POSTs them to `POST /inbox/assignments/{id}/complete`, which (server-side)
 * creates a HumanWorkSession and transitions the task to `in_review`.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                                  — a plain TENANT USER
 *   - GET  /inbox/assignments                   — one ACCEPTED assignment
 *   - POST /inbox/assignments/{id}/complete     — captures the structured body
 *
 * NOTE: WRITTEN but NOT run as part of task_16_09 — PENDING HUMAN VERIFICATION
 * (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/human-task-submit.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

const TENANT_USER = {
  user_id: "aaaa0000-0000-0000-0000-000000000001",
  email: "alice@a.test",
  full_name: "Alice",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_user", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const ACCEPTED = {
  assignment_id: "asg-accepted-1",
  task_id: "task-accepted-1",
  human_agent_id: "ha-1",
  assignment_status: "accepted",
  task_status: "in_progress",
  assigned_at: new Date().toISOString(),
  acceptance_deadline: null,
  task_title: "Revisión legal del contrato",
  task_description: "Revisar las cláusulas 3 y 7 antes de la firma.",
  project_id: "proj-1",
  project_name: "Proyecto A",
  plan_id: "plan-1",
  plan_title: "Plan de cierre",
};

async function setup(
  page: Page,
  opts: { onSubmit?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_USER),
    }),
  );

  let submitted = false;

  // The complete (submit) endpoint — keep it BEFORE the bare list route so it wins.
  await page.route("**/inbox/assignments/*/complete", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onSubmit?.(body);
    submitted = true;
    const attachments = Array.isArray(body.attachments) ? body.attachments.length : 0;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        assignment_id: ACCEPTED.assignment_id,
        task_id: ACCEPTED.task_id,
        action: "complete",
        assignment_status: "accepted",
        task_status: "in_review",
        work_session_id: "ws-1",
        attachments_count: attachments,
      }),
    });
  });

  await page.route("**/inbox/assignments", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      // Once submitted, the task is in_review — the row drops out of the active inbox.
      body: JSON.stringify(submitted ? [] : [ACCEPTED]),
    }),
  );
}

test("opening the delivery form: submit is disabled until there is a deliverable", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  // Empty form -> nothing to deliver -> submit disabled.
  await expect(page.getByTestId("submit-confirm")).toBeDisabled();
  // Typing an output enables it.
  await page.getByTestId("submit-output-edit").fill("Revisión completada sin observaciones.");
  await expect(page.getByTestId("submit-confirm")).toBeEnabled();
});

test("submitting posts output + attachments + hours", async ({ page }) => {
  let captured: Record<string, unknown> | null = null;
  await setup(page, { onSubmit: (body) => (captured = body) });
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  await page
    .getByTestId("submit-output-edit")
    .fill("Cláusulas 3 y 7 OK; anexo 2 con observaciones.");

  // Add a URL attachment.
  await page.getByTestId("submit-add-url").click();
  const urlRow = page.locator('[data-testid^="submit-attachment-"]').first();
  await urlRow.getByRole("textbox", { name: "Etiqueta del adjunto" }).fill("PR de cambios");
  await urlRow.getByRole("textbox", { name: "URL del adjunto" }).fill("https://example.test/pr/42");

  // The valid-count indicator reflects the usable attachment.
  await expect(page.getByTestId("submit-attachment-count")).toContainText("1");

  // Log hours.
  await page.getByTestId("submit-hours").fill("3.5");

  await page.getByTestId("submit-confirm").click();

  await expect.poll(() => captured !== null).toBeTruthy();
  expect(captured).toMatchObject({
    output: "Cláusulas 3 y 7 OK; anexo 2 con observaciones.",
    hours_worked: 3.5,
    attachments: [{ kind: "url", label: "PR de cambios", url: "https://example.test/pr/42" }],
  });

  // After submit the row drops out of the active inbox (task -> in_review).
  await expect(page.getByTestId(`inbox-assignment-${ACCEPTED.assignment_id}`)).toHaveCount(0);
});

test("an incomplete attachment is not counted as valid", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  // Add a URL row but leave it blank -> 0 valid -> still cannot submit.
  await page.getByTestId("submit-add-url").click();
  await expect(page.getByTestId("submit-attachment-count")).toContainText("0");
  await expect(page.getByTestId("submit-confirm")).toBeDisabled();

  // A label without a target is still not usable.
  const row = page.locator('[data-testid^="submit-attachment-"]').first();
  await row.getByRole("textbox", { name: "Etiqueta del adjunto" }).fill("captura");
  await expect(page.getByTestId("submit-attachment-count")).toContainText("0");
  await expect(page.getByTestId("submit-confirm")).toBeDisabled();
});

test("negative hours are rejected client-side", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  await page.getByTestId("submit-output-edit").fill("Trabajo hecho.");
  await page.getByTestId("submit-hours").fill("-2");

  await expect(page.getByTestId("submit-hours-error")).toBeVisible();
  await expect(page.getByTestId("submit-confirm")).toBeDisabled();
});

test("an attachment can be removed", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  await page.getByTestId("submit-add-file").click();
  const row = page.locator('[data-testid^="submit-attachment-"]').first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Quitar adjunto" }).click();
  await expect(page.locator('[data-testid^="submit-attachment-"]')).toHaveCount(0);
});
