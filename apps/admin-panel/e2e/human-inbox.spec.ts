import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the personal inbox — "Tareas asignadas a mí" (Plan 16 task_16_08).
 *
 * The inbox (/admin/inbox) lists the CALLER user's own active human-task
 * assignments and offers the four contextual actions: accept, reject (with a
 * required justification), mark complete, escalate to admin. It is NOT
 * admin-only — any tenant member sees their own tray.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                                        — a plain TENANT USER
 *   - GET  /inbox/assignments                         — the caller's list
 *   - POST /inbox/assignments/{id}/accept             — pending -> accepted
 *   - POST /inbox/assignments/{id}/reject             — needs { justification }
 *   - POST /inbox/assignments/{id}/complete           — accepted -> in_review
 *   - POST /inbox/assignments/{id}/escalate           — -> blocked + notify admin
 *
 * NOTE: WRITTEN but NOT run as part of task_16_08 — PENDING HUMAN VERIFICATION
 * (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/human-inbox.spec.ts`.
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

const PENDING = {
  assignment_id: "asg-pending-1",
  task_id: "task-pending-1",
  human_agent_id: "ha-1",
  assignment_status: "pending_acceptance",
  task_status: "assigned_to_human",
  assigned_at: new Date().toISOString(),
  acceptance_deadline: new Date(Date.now() + 6 * 3600 * 1000).toISOString(),
  task_title: "Revisión legal del contrato",
  task_description: "Revisar las cláusulas 3 y 7 antes de la firma.",
  project_id: "proj-1",
  project_name: "Proyecto A",
  plan_id: "plan-1",
  plan_title: "Plan de cierre",
};

const ACCEPTED = {
  assignment_id: "asg-accepted-1",
  task_id: "task-accepted-1",
  human_agent_id: "ha-1",
  assignment_status: "accepted",
  task_status: "in_progress",
  assigned_at: new Date().toISOString(),
  acceptance_deadline: null,
  task_title: "Auditoría de seguridad",
  task_description: null,
  project_id: "proj-1",
  project_name: "Proyecto A",
  plan_id: null,
  plan_title: null,
};

async function setup(
  page: Page,
  opts: { onAction?: (path: string, body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_USER),
    }),
  );

  // Mutable server-side state for the two assignments.
  let pendingStatus = {
    assignment_status: PENDING.assignment_status,
    task_status: PENDING.task_status,
  };
  let pendingVisible = true;

  // Action endpoints — keep these BEFORE the bare list route so they win.
  await page.route("**/inbox/assignments/*/**", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/");
    const action = parts[parts.length - 1];
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onAction?.(url.pathname, body);

    if (action === "reject" && !body.justification) {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "a justification is required to reject an assignment" }),
      });
    }
    if (action === "accept") {
      pendingStatus = { assignment_status: "accepted", task_status: "in_progress" };
    } else {
      // reject / escalate close the pending row; complete moves it to in_review.
      pendingVisible = false;
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        assignment_id: PENDING.assignment_id,
        task_id: PENDING.task_id,
        action,
        ...pendingStatus,
      }),
    });
  });

  await page.route("**/inbox/assignments", (route) => {
    const rows = [];
    if (pendingVisible) rows.push({ ...PENDING, ...pendingStatus });
    rows.push(ACCEPTED);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(rows),
    });
  });
}

test("the inbox lists the caller's own assignments with status + context", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`inbox-assignment-${PENDING.assignment_id}`)).toBeVisible();
  await expect(page.getByTestId(`inbox-assignment-${ACCEPTED.assignment_id}`)).toBeVisible();
  // The pending row shows the "Asignada" status badge and a deadline.
  await expect(page.getByTestId(`inbox-status-${PENDING.assignment_id}`)).toContainText("Asignada");
  await expect(page.getByTestId(`inbox-deadline-${PENDING.assignment_id}`)).toBeVisible();
});

test("a pending assignment offers accept + reject; accept transitions it", async ({ page }) => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  await setup(page, { onAction: (path, body) => calls.push({ path, body }) });
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`inbox-accept-${PENDING.assignment_id}`)).toBeVisible();
  await expect(page.getByTestId(`inbox-reject-${PENDING.assignment_id}`)).toBeVisible();

  await page.getByTestId(`inbox-accept-${PENDING.assignment_id}`).click();

  await expect.poll(() => calls.length).toBeGreaterThanOrEqual(1);
  expect(calls[0].path).toContain(`/inbox/assignments/${PENDING.assignment_id}/accept`);
  // After accept, the row reflects the in_progress task status.
  await expect(page.getByTestId(`inbox-status-${PENDING.assignment_id}`)).toContainText("En curso");
});

test("reject requires a justification before confirming", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-reject-${PENDING.assignment_id}`).click();
  // The confirm button is disabled until a justification is typed.
  await expect(page.getByTestId("inbox-action-confirm")).toBeDisabled();
  await page.getByTestId("inbox-action-text-edit").fill("Fuera de mi área de competencia");
  await expect(page.getByTestId("inbox-action-confirm")).toBeEnabled();
});

test("reject with a justification posts it and removes the row", async ({ page }) => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  await setup(page, { onAction: (path, body) => calls.push({ path, body }) });
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-reject-${PENDING.assignment_id}`).click();
  await page.getByTestId("inbox-action-text-edit").fill("Fuera de mi área de competencia");
  await page.getByTestId("inbox-action-confirm").click();

  await expect.poll(() => calls.length).toBeGreaterThanOrEqual(1);
  const reject = calls.find((c) => c.path.endsWith("/reject"));
  expect(reject).toBeTruthy();
  expect(reject?.body).toMatchObject({ justification: "Fuera de mi área de competencia" });
  // The pending row is gone (declined); the accepted one remains.
  await expect(page.getByTestId(`inbox-assignment-${PENDING.assignment_id}`)).toHaveCount(0);
  await expect(page.getByTestId(`inbox-assignment-${ACCEPTED.assignment_id}`)).toBeVisible();
});

test("an accepted assignment can be marked complete", async ({ page }) => {
  // Reparado el 2026-08-19: "Marcar completada" ya NO abre el diálogo de
  // justificación (`inbox-action-text`), abre el FORMULARIO DE ENTREGA
  // (`submit-dialog.tsx`), que es lo que hace del cierre una entrega y no un
  // comentario: el cuerpo del POST /complete lleva `output` + `attachments`
  // (+ horas), no `comments`. El detalle del formulario lo cubre
  // `human-task-submit.spec.ts`; aquí sólo se comprueba que el botón de la
  // bandeja lleva a él y que la entrega viaja.
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  await setup(page, { onAction: (path, body) => calls.push({ path, body }) });
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`)).toBeVisible();
  await page.getByTestId(`inbox-complete-${ACCEPTED.assignment_id}`).click();
  await page.getByTestId("submit-output-edit").fill("Listo, sin observaciones");
  await page.getByTestId("submit-confirm").click();

  await expect.poll(() => calls.length).toBeGreaterThanOrEqual(1);
  const complete = calls.find((c) => c.path.endsWith("/complete"));
  expect(complete).toBeTruthy();
  expect(complete?.body).toMatchObject({
    output: "Listo, sin observaciones",
    attachments: [],
  });
});

test("any assignment can be escalated to the admin", async ({ page }) => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  await setup(page, { onAction: (path, body) => calls.push({ path, body }) });
  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`inbox-escalate-${PENDING.assignment_id}`).click();
  // Escalate's reason is optional — confirm is enabled with an empty field.
  await expect(page.getByTestId("inbox-action-confirm")).toBeEnabled();
  await page.getByTestId("inbox-action-confirm").click();

  await expect.poll(() => calls.length).toBeGreaterThanOrEqual(1);
  const escalate = calls.find((c) => c.path.endsWith("/escalate"));
  expect(escalate).toBeTruthy();
});

test("an empty inbox shows the empty state", async ({ page }) => {
  await seedSession(page, { tenantId: TENANT_ID });
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_USER),
    }),
  );
  await page.route("**/inbox/assignments", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.goto("/admin/inbox", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("inbox-empty")).toBeVisible();
});
