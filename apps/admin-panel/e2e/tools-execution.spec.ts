import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for the UI-level "a real assigned tool executes (not 'unknown tool')"
 * invariant (Plan 06.18 task_06_18_14, ADR 0048/0049).
 *
 * This is the UI complement to the runtime-level proof already covered by
 * `docker/agent-runtimes/agent-runtime/tests/test_runtime_boot_tools.py`
 * ::test_assigned_read_file_is_wired_and_runs (an assigned `read_file`
 * is wired and runs, never returns "unknown tool") and to the catalog-level
 * contract in `tests/integration/test_tool_catalog_contract.py`.
 *
 * The class of bug ADR 0048/0049 kills is silent: an operator assigns a tool
 * the runtime cannot execute, the assignment "saves", and the agent later dies
 * mid-run as a silent `unknown tool`. The UI must make that impossible at
 * assignment time:
 *
 *   1. Assigning a runtime-WIRED builtin (`read_file`) saves with 200 — the
 *      operator hands the agent a tool that will actually run. This is the UI
 *      reflection of the runtime test: what saves here is exactly the tool the
 *      runtime boots and executes.
 *   2. Assigning a builtin with NO runtime executor (`apply_patch`) is rejected
 *      by `PUT /agents/{id}/tools` with 422, and the section surfaces the error
 *      (with the offending tool name) instead of silently persisting a dead
 *      assignment. The operator never ships something that would become a
 *      runtime `unknown tool`.
 *   3. `semantic_search` (the catalog/knowledge name) is assignable and saves —
 *      it reconciles onto the runtime's `rag_search`, so it is wired, not
 *      orphaned (ADR 0049). The operator assigns the friendly catalog name and
 *      the runtime still executes it.
 *
 * The backend that derives `is_runtime_wired` and returns the 422 is exercised
 * by `tests/integration/test_tool_runtime_availability.py`; here we drive the
 * UI contract on top of mocked endpoints so the two layers are pinned from both
 * ends.
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_06_18_14 — it is
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server; no
 * live stack exists in the implementation environment). Run with
 * `npx playwright test e2e/tools-execution.spec.ts`.
 */

const API = "http://localhost:8001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";

// Mirrors the real seed: read_file is a wired builtin, apply_patch is a builtin
// with no runtime executor (is_runtime_wired=false), semantic_search reconciles
// onto rag_search so it is wired.
const READ_FILE_ID = "44444444-0000-0000-0000-000000000001";
const APPLY_PATCH_ID = "44444444-0000-0000-0000-000000000002";
const SEMANTIC_SEARCH_ID = "44444444-0000-0000-0000-000000000003";

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

function agentBody() {
  return {
    id: AGENT_ID,
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
    scope: "project_local",
    project_id: null,
    forked_from_agent_id: null,
    forked_from_version: null,
    anchored_version: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    deleted_at: null,
  };
}

// Three builtins in the `file` / `knowledge` groups, each carrying the
// `is_runtime_wired` flag the backend derives (ADR 0049).
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
    is_runtime_wired: true,
  },
  {
    id: APPLY_PATCH_ID,
    tenant_id: TENANT_ID,
    name: "apply_patch",
    description: "Applies a unified-diff patch (no runtime executor yet).",
    category: "file",
    implementation_type: "builtin",
    security_level: "sandboxed",
    is_builtin: true,
    is_runtime_wired: false,
  },
  {
    id: SEMANTIC_SEARCH_ID,
    tenant_id: TENANT_ID,
    name: "semantic_search",
    description: "Semantic search over the project knowledge bases.",
    category: "knowledge",
    implementation_type: "builtin",
    security_level: "safe",
    is_builtin: true,
    is_runtime_wired: true,
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
    assigned?: string[];
    // Tool ids the backend refuses (non-executable builtin) → 422 with the
    // offending tool name in the detail, exactly like
    // PUT /agents/{id}/tools does for apply_patch in the integration test.
    rejectIds?: string[];
    onPut?: (body: unknown) => void;
  } = {},
): Promise<void> {
  const assigned = opts.assigned ?? [];
  const rejectIds = new Set(opts.rejectIds ?? []);

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

  await page.route(`${API}/me`, (route) => json(route, TENANT_ADMIN));
  await page.route(`${API}/agents/${AGENT_ID}`, (route) => json(route, agentBody()));
  await page.route(`${API}/tools**`, (route) => json(route, CATALOG));
  await page.route(`${API}/agents/${AGENT_ID}/knowledge-bases`, (route) => json(route, []));
  await page.route(`${API}/agents/${AGENT_ID}/tools`, async (route) => {
    if (route.request().method() === "PUT") {
      const sent = route.request().postDataJSON() as { tools: { tool_id: string }[] };
      opts.onPut?.(sent);
      const offending = sent.tools.find((t) => rejectIds.has(t.tool_id));
      if (offending) {
        const tool = CATALOG.find((c) => c.id === offending.tool_id)!;
        await json(
          route,
          {
            detail:
              `La tool '${tool.name}' no es ejecutable en el runtime ` +
              "(sin executor cableado); no puede asignarse.",
          },
          422,
        );
        return;
      }
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
// 1. Assigning a runtime-WIRED builtin saves — the agent gets a tool that runs.
// ---------------------------------------------------------------------------
test("assigning a wired builtin (read_file) saves and confirms", async ({ page }) => {
  let putBody: { tools: { tool_id: string }[] } | null = null;
  await setup(page, {
    assigned: [],
    onPut: (body) => {
      putBody = body as { tools: { tool_id: string }[] };
    },
  });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId(`agent-tool-row-${READ_FILE_ID}`).getByText("read_file").click();
  await page.getByTestId("agent-tools-save").click();

  // The save confirms (no error) and the body carries exactly the wired tool —
  // the same tool the runtime test proves boots and executes.
  await expect(page.getByTestId("agent-tools-saved")).toBeVisible();
  await expect(page.getByTestId("agent-tools-save-error")).toHaveCount(0);
  await expect.poll(() => putBody).not.toBeNull();
  expect(putBody!.tools.map((t) => t.tool_id)).toEqual([READ_FILE_ID]);
});

// ---------------------------------------------------------------------------
// 2. Assigning a NON-executable builtin is refused (422) and surfaced — the
//    operator never ships an assignment that becomes a runtime "unknown tool".
// ---------------------------------------------------------------------------
test("assigning a non-executable builtin (apply_patch) is rejected and surfaced", async ({
  page,
}) => {
  await setup(page, { assigned: [], rejectIds: [APPLY_PATCH_ID] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId(`agent-tool-row-${APPLY_PATCH_ID}`).getByText("apply_patch").click();
  await page.getByTestId("agent-tools-save").click();

  // The 422 from the runtime-wired guard surfaces with the offending tool name;
  // no silent "saved" confirmation appears.
  const err = page.getByTestId("agent-tools-save-error");
  await expect(err).toBeVisible();
  await expect(err).toContainText("apply_patch");
  await expect(page.getByTestId("agent-tools-saved")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 3. semantic_search reconciles onto rag_search → assignable and saves.
// ---------------------------------------------------------------------------
test("assigning semantic_search saves (reconciles onto rag_search)", async ({ page }) => {
  let putBody: { tools: { tool_id: string }[] } | null = null;
  await setup(page, {
    assigned: [],
    onPut: (body) => {
      putBody = body as { tools: { tool_id: string }[] };
    },
  });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  // semantic_search lives under the knowledge group — still a Básica builtin.
  await page
    .getByTestId(`agent-tool-row-${SEMANTIC_SEARCH_ID}`)
    .getByText("semantic_search")
    .click();
  await page.getByTestId("agent-tools-save").click();

  await expect(page.getByTestId("agent-tools-saved")).toBeVisible();
  await expect(page.getByTestId("agent-tools-save-error")).toHaveCount(0);
  await expect.poll(() => putBody).not.toBeNull();
  expect(putBody!.tools.map((t) => t.tool_id)).toEqual([SEMANTIC_SEARCH_ID]);
});
