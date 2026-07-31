import { expect, test, type Page, type Route } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the affordance overhaul of the "Tools del agente" section in
 * /admin/agents/{id} (Plan 06.18 task_06_18_09).
 *
 * Builds on task_06_15_03's assignment UI but verifies the *affordance*
 * fixes called out by the operator ("no se sabe si algo es clickable",
 * "tools duplicadas"):
 *   - the whole tool row is a single control: clicking anywhere in the
 *     row's toggle area (name, blank space) flips the checkbox,
 *   - read-only rows expose NO pointer cursor and do NOT toggle,
 *   - a selected row is distinguishable at a glance via the strong
 *     selected styling (asserted through the stable `data-selected`
 *     attribute the styling is bound to),
 *   - the security + implementation badges are flat informative chips
 *     with an accessible Tooltip that opens on hover AND on keyboard
 *     focus (the trigger gains `aria-describedby` → the `role="tooltip"`
 *     panel becomes visible),
 *   - the per-group "Seleccionar/Quitar todas" control is a tri-state
 *     checkbox (unchecked → indeterminate → checked).
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_06_18_09 — it is
 * PENDING HUMAN VERIFICATION (needs a browser + admin-panel dev server +
 * a live backend, none of which exist in the implementation environment).
 * Run with `npx playwright test e2e/tools-affordance.spec.ts`.
 */

const API = "http://localhost:8001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";
const BUILTIN_AGENT_ID = "33333333-0000-0000-0000-0000000000bb";

const READ_FILE_ID = "44444444-0000-0000-0000-000000000001";
const WRITE_FILE_ID = "44444444-0000-0000-0000-000000000002";
const RUN_TESTS_ID = "44444444-0000-0000-0000-000000000005";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_admin",
      is_active: true,
    },
  ],
  active_tenant_id: TENANT_ID,
};

function agentBody(scope: string) {
  return {
    id: scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID,
    tenant_id: TENANT_ID,
    name: "Backend Dev",
    description: "Builds backend features.",
    avatar_url: null,
    agent_type: "ai",
    role: "backend_dev",
    system_prompt: "You are a backend dev.",
    model_config: {},
    memory_scope: "private",
    review_capability: false,
    max_concurrent_tasks: 1,
    is_template: false,
    scope,
    project_id: null,
    forked_from_agent_id: null,
    forked_from_version: null,
    anchored_version: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    deleted_at: null,
  };
}

// Two builtin file tools + one builtin runtime tool, so the "file" group
// has two members (needed to exercise the tri-state select-all).
const CATALOG = [
  {
    id: READ_FILE_ID,
    tenant_id: TENANT_ID,
    name: "read_file",
    description: "Reads a file from the project repo.",
    category: "file",
    implementation_type: "builtin",
    security_level: "safe",
    is_builtin: true,
  },
  {
    id: WRITE_FILE_ID,
    tenant_id: TENANT_ID,
    name: "write_file",
    description: "Writes a file in the task worktree.",
    category: "file",
    implementation_type: "builtin",
    security_level: "sandboxed",
    is_builtin: true,
  },
  {
    id: RUN_TESTS_ID,
    tenant_id: TENANT_ID,
    name: "run_tests",
    description: "Runs the test suite in an ephemeral container.",
    category: "runtime",
    implementation_type: "docker_command",
    security_level: "privileged",
    is_builtin: true,
  },
];

function assignedRow(id: string) {
  const t = CATALOG.find((c) => c.id === id)!;
  return {
    tool_id: t.id,
    name: t.name,
    description: t.description,
    category: t.category,
    implementation_type: t.implementation_type,
    security_level: t.security_level,
    is_builtin: t.is_builtin,
    config_override: null,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setup(
  page: Page,
  opts: {
    scope?: string;
    assigned?: string[];
    onPut?: (body: unknown) => void;
  } = {},
): Promise<void> {
  const scope = opts.scope ?? "project_local";
  const id = scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID;
  const assigned = opts.assigned ?? [];

  await seedSession(page);

  await page.route(`${API}/me`, (route) => json(route, TENANT_ADMIN));
  await page.route(`${API}/agents/${id}`, (route) => json(route, agentBody(scope)));
  await page.route(`${API}/tools**`, (route) => json(route, CATALOG));
  await page.route(`${API}/agents/${id}/knowledge-bases`, (route) => json(route, []));
  await page.route(`${API}/agents/${id}/tools`, async (route) => {
    if (route.request().method() === "PUT") {
      opts.onPut?.(route.request().postDataJSON());
      const sent = route.request().postDataJSON() as { tools: { tool_id: string }[] };
      await json(
        route,
        sent.tools.map((t) => assignedRow(t.tool_id)),
      );
      return;
    }
    await json(route, assigned.map(assignedRow));
  });
}

// ---------------------------------------------------------------------------
// Whole-row click toggles (hover area === toggle area)
// ---------------------------------------------------------------------------
test("clicking anywhere in the row toggles the tool", async ({ page }) => {
  await setup(page, { assigned: [] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  const row = page.getByTestId(`agent-tool-row-${READ_FILE_ID}`);
  const checkbox = page.getByTestId(`agent-tool-checkbox-${READ_FILE_ID}`);
  await expect(checkbox).not.toBeChecked();
  await expect(row).toHaveAttribute("data-selected", "false");

  // Click on the tool NAME (not directly on the checkbox) — the row is a
  // single control, so this must flip the box.
  await row.getByText("read_file").click();
  await expect(checkbox).toBeChecked();
  await expect(row).toHaveAttribute("data-selected", "true");

  // Click again to toggle off.
  await row.getByText("read_file").click();
  await expect(checkbox).not.toBeChecked();
  await expect(row).toHaveAttribute("data-selected", "false");
});

// ---------------------------------------------------------------------------
// Selected row is distinguishable at a glance (strong selected state)
// ---------------------------------------------------------------------------
test("selected row is visually distinct without inspecting the checkbox", async ({ page }) => {
  await setup(page, { assigned: [READ_FILE_ID] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  const selectedRow = page.getByTestId(`agent-tool-row-${READ_FILE_ID}`);
  const unselectedRow = page.getByTestId(`agent-tool-row-${WRITE_FILE_ID}`);

  // The selected styling (border-primary/60 + bg-primary/5) is bound to
  // this attribute, so it is the stable proxy for "looks selected".
  await expect(selectedRow).toHaveAttribute("data-selected", "true");
  await expect(unselectedRow).toHaveAttribute("data-selected", "false");
});

// ---------------------------------------------------------------------------
// Read-only: no pointer cursor, no toggle
// ---------------------------------------------------------------------------
test("read-only agent: row does not toggle and shows no pointer cursor", async ({ page }) => {
  await setup(page, { scope: "global_builtin", assigned: [READ_FILE_ID] });
  await page.goto(`/admin/agents/${BUILTIN_AGENT_ID}`, { waitUntil: "domcontentloaded" });

  const checkbox = page.getByTestId(`agent-tool-checkbox-${READ_FILE_ID}`);
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toBeDisabled();

  // The toggle label uses the default cursor (not a pointer) when read-only.
  const label = page.locator(`label[for="agent-tool-${READ_FILE_ID}"]`);
  await expect(label).toHaveCSS("cursor", "default");

  // Clicking the disabled control must NOT flip it.
  await page
    .getByTestId(`agent-tool-row-${READ_FILE_ID}`)
    .getByText("read_file")
    .click({ force: true });
  await expect(checkbox).toBeChecked();

  // And there is no Save affordance at all.
  await expect(page.getByTestId("agent-tools-save")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Tooltip opens on hover AND on keyboard focus, with correct ARIA wiring
// ---------------------------------------------------------------------------
test("security badge tooltip opens on hover and on keyboard focus", async ({ page }) => {
  await setup(page, { assigned: [] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  const trigger = page.getByTestId(`agent-tool-security-badge-${READ_FILE_ID}`);
  const tip = page.getByRole("tooltip").filter({ hasText: "Solo lectura" });

  // Hidden by default.
  await expect(tip).toBeHidden();

  // Hover → visible + trigger describes the panel.
  await trigger.hover();
  await expect(tip).toBeVisible();
  const describedBy = await trigger.getAttribute("aria-describedby");
  expect(describedBy).not.toBeNull();

  // Move away → hidden again.
  await page.mouse.move(0, 0);
  await expect(tip).toBeHidden();

  // Keyboard focus → visible (parity with mouse users).
  await trigger.focus();
  await expect(tip).toBeVisible();
});

// ---------------------------------------------------------------------------
// Per-group select-all is a tri-state checkbox
// ---------------------------------------------------------------------------
test("group select-all is a tri-state checkbox", async ({ page }) => {
  // file group seeded with ONE of TWO members selected → indeterminate.
  await setup(page, { assigned: [READ_FILE_ID] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  const bulk = page.getByTestId("agent-tools-group-toggle-file");

  // Partially selected → aria-checked="mixed" (indeterminate).
  await expect(bulk).toHaveJSProperty("indeterminate", true);

  // Click → selects all in the group (no longer mixed, fully checked).
  await bulk.click();
  await expect(bulk).toHaveJSProperty("indeterminate", false);
  await expect(bulk).toBeChecked();
  await expect(page.getByTestId(`agent-tool-checkbox-${WRITE_FILE_ID}`)).toBeChecked();

  // Click again → clears the whole group.
  await bulk.click();
  await expect(bulk).not.toBeChecked();
  await expect(bulk).toHaveJSProperty("indeterminate", false);
  await expect(page.getByTestId(`agent-tool-checkbox-${READ_FILE_ID}`)).not.toBeChecked();
});

// ---------------------------------------------------------------------------
// "Guardado" feedback after a successful save (consistent w/ Comandos)
// ---------------------------------------------------------------------------
test("shows Guardado confirmation after saving", async ({ page }) => {
  await setup(page, { assigned: [] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("agent-tools-saved")).toHaveCount(0);

  await page.getByTestId(`agent-tool-row-${READ_FILE_ID}`).getByText("read_file").click();
  await page.getByTestId("agent-tools-save").click();

  await expect(page.getByTestId("agent-tools-saved")).toBeVisible();
  await expect(page.getByTestId("agent-tools-saved")).toContainText("Guardado");
});
