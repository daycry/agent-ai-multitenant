import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the Human Agents gallery — CREATE flow (Plan 16 task_16_07).
 *
 * The gallery (/admin/human-agents) lists the tenant's Human Agents and offers a
 * create/edit form with the full human_agent_config (assigned user, hourly rate
 * + currency, notification channels, acceptance timeout, expected times,
 * escalation target). assignment_mode is fixed to specific_user (MVP).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                              — a TENANT ADMIN
 *   - GET  /human-agents                    — the tenant's list (empty, then 1)
 *   - GET  /human-agents/templates          — global catalog (empty here)
 *   - GET  /human-agents/assignable-users   — two pickable members
 *   - POST /human-agents                    — persists, echoes the payload
 *
 * NOTE: WRITTEN but NOT run as part of task_16_07 — PENDING HUMAN VERIFICATION
 * (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/human-agent-create.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@platform.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const USERS = [
  {
    user_id: "aaaa0000-0000-0000-0000-000000000001",
    email: "alice@a.test",
    full_name: "Alice",
    role: "tenant_user",
  },
  {
    user_id: "bbbb0000-0000-0000-0000-000000000002",
    email: "bob@a.test",
    full_name: "Bob",
    role: "tenant_admin",
  },
];

async function setup(
  page: Page,
  opts: { onCreate?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  let created = false;

  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_ADMIN),
    }),
  );

  await page.route("**/human-agents/templates**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.route("**/human-agents/assignable-users**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(USERS),
    }),
  );

  // The bare list endpoint (GET list / POST create). Keep this AFTER the more
  // specific /templates + /assignable-users routes so they win.
  await page.route("**/human-agents", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      const body = created
        ? JSON.stringify([
            {
              id: "ha-new-1",
              tenant_id: TENANT_ID,
              name: "Security Reviewer",
              description: null,
              avatar_url: null,
              agent_type: "human",
              role: "security",
              scope: "global_tenant_template",
              is_template: true,
              forked_from_agent_id: null,
              config: {
                id: "cfg-1",
                agent_id: "ha-new-1",
                assignment_mode: "specific_user",
                assigned_user_id: USERS[0].user_id,
                hourly_rate: "90.00",
                hourly_rate_currency: "EUR",
                notification_channels: ["email", "in_app"],
                acceptance_timeout_hours: 12,
                escalation_target_user_id: null,
                expected_response_time_hours: null,
                expected_execution_time_hours: null,
              },
            },
          ])
        : "[]";
      return route.fulfill({ status: 200, contentType: "application/json", body });
    }
    if (method === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onCreate?.(body);
      created = true;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "ha-new-1",
          tenant_id: TENANT_ID,
          agent_type: "human",
          scope: "global_tenant_template",
          is_template: true,
          forked_from_agent_id: null,
          ...body,
          config: {
            id: "cfg-1",
            agent_id: "ha-new-1",
            assignment_mode: "specific_user",
            ...(body.config ?? {}),
          },
        }),
      });
    }
    return route.fallback();
  });
}

test("new human agent button is visible for a tenant admin", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("new-human-agent-button")).toBeVisible();
  await expect(page.getByTestId("human-agents-empty")).toBeVisible();
});

test("form opens with the full human_agent_config fields", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-human-agent-button").click();

  await expect(page.getByTestId("ha-name")).toBeVisible();
  await expect(page.getByTestId("ha-role")).toBeVisible();
  await expect(page.getByTestId("ha-assigned-user")).toBeVisible();
  await expect(page.getByTestId("ha-escalation-user")).toBeVisible();
  await expect(page.getByTestId("ha-hourly-rate")).toBeVisible();
  await expect(page.getByTestId("ha-currency")).toBeVisible();
  await expect(page.getByTestId("ha-acceptance-timeout")).toBeVisible();
  await expect(page.getByTestId("ha-channel-email")).toBeVisible();
  await expect(page.getByTestId("ha-expected-response")).toBeVisible();
  await expect(page.getByTestId("ha-expected-execution")).toBeVisible();
});

test("the assigned-user picker is populated from assignable-users", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-human-agent-button").click();
  // Alice + Bob are present as options.
  await expect(page.getByTestId("ha-assigned-user").locator("option")).toContainText([
    "Alice",
    "Bob",
  ]);
});

test("submit posts a cohesive agent + config payload and the list refreshes", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onCreate: (body) => calls.push(body) });
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });

  await page.getByTestId("new-human-agent-button").click();
  await page.getByTestId("ha-name").fill("Security Reviewer");
  await page.getByTestId("ha-role").selectOption("security");
  await page.getByTestId("ha-assigned-user").selectOption(USERS[0].user_id);
  await page.getByTestId("ha-hourly-rate").fill("90.00");
  await page.getByTestId("ha-acceptance-timeout").fill("12");
  await page.getByTestId("ha-submit").click();

  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toMatchObject({
    name: "Security Reviewer",
    role: "security",
    config: {
      assigned_user_id: USERS[0].user_id,
      hourly_rate: "90.00",
      hourly_rate_currency: "EUR",
      acceptance_timeout_hours: 12,
    },
  });

  // After save, the new agent appears in the list.
  await expect(page.getByTestId("human-agent-ha-new-1")).toBeVisible();
  await expect(page.getByTestId("ha-assigned-ha-new-1")).toContainText("Alice");
});

test("submit is disabled with an empty name", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-human-agent-button").click();
  await expect(page.getByTestId("ha-submit")).toBeDisabled();
});
