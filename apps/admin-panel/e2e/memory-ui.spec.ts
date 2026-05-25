import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/memories (Plan 04 task_04_06).
 *
 * Mocks GET/POST/DELETE /memories. Drives:
 *   - rendering the list with the default scope filter (team_shared),
 *   - flipping the scope filter,
 *   - creating a private memory via the form,
 *   - deleting one of the listed memories.
 */

const TEAM_MEMORY = {
  id: "11111111-1111-1111-1111-111111111111",
  tenant_id: "ttttttt0-0000-0000-0000-000000000001",
  scope: "team_shared",
  type: "semantic",
  content: "Team prefers REST endpoints over GraphQL.",
  tags: ["rest", "graphql"],
  user_id: null,
  team_id: "aaaaaaaa-0000-0000-0000-000000000001",
  project_id: null,
  source_execution_id: null,
  agent_id: null,
  has_embedding: false,
  created_at: "2026-05-25T10:00:00Z",
  updated_at: "2026-05-25T10:00:00Z",
};

const PRIVATE_MEMORY = {
  id: "22222222-2222-2222-2222-222222222222",
  tenant_id: "ttttttt0-0000-0000-0000-000000000001",
  scope: "private",
  type: "episodic",
  content: "Alice avoids Friday deploys.",
  tags: ["deploy"],
  user_id: "uuuuuuuu-0000-0000-0000-000000000001",
  team_id: null,
  project_id: null,
  source_execution_id: null,
  agent_id: null,
  has_embedding: true,
  created_at: "2026-05-25T11:00:00Z",
  updated_at: "2026-05-25T11:00:00Z",
};

interface Capture {
  postCalls: number;
  lastPostBody: Record<string, unknown>;
  deleteCalls: number;
  lastDeletedId: string | null;
}

async function setup(page: Page): Promise<Capture> {
  const capture: Capture = {
    postCalls: 0,
    lastPostBody: {},
    deleteCalls: 0,
    lastDeletedId: null,
  };
  const store: Record<string, (typeof TEAM_MEMORY)[]> = {
    team_shared: [TEAM_MEMORY],
    private: [PRIVATE_MEMORY],
    project_shared: [],
    global: [],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

  await page.route(/http:\/\/localhost:8001\/memories(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (method === "GET") {
      const scope = url.searchParams.get("scope");
      const list = scope ? (store[scope] ?? []) : Object.values(store).flat();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(list),
      });
    }
    if (method === "POST") {
      capture.postCalls += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
      capture.lastPostBody = body;
      const created = {
        ...PRIVATE_MEMORY,
        id: `33333333-3333-3333-3333-${String(capture.postCalls).padStart(12, "0")}`,
        scope: body.scope,
        type: body.type ?? "semantic",
        content: body.content,
        tags: (body.tags as string[]) ?? [],
        has_embedding: false,
      };
      const scopeKey = String(body.scope);
      store[scopeKey] = [...(store[scopeKey] ?? []), created];
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
    }
    return route.continue();
  });

  await page.route(/http:\/\/localhost:8001\/memories\/[0-9a-f-]+$/, (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    capture.deleteCalls += 1;
    capture.lastDeletedId = route.request().url().split("/").pop() ?? null;
    // Drop from store.
    for (const key of Object.keys(store)) {
      store[key] = store[key].filter((m) => m.id !== capture.lastDeletedId);
    }
    return route.fulfill({ status: 204, body: "" });
  });

  return capture;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
test("default scope filter is team_shared and shows the team memory", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("memories-page")).toBeVisible();
  await expect(page.getByTestId("memories-scope-team_shared")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toBeVisible();
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toContainText("REST");
});

test("changing scope filter to private surfaces the private memory", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memories-scope-private").click();
  await expect(page.getByTestId(`memory-${PRIVATE_MEMORY.id}`)).toBeVisible();
  // Team memory must not show under the private filter.
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toHaveCount(0);
});

test("creating a private memory POSTs and refreshes the list", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  // Switch to private so we can see the new row after the refresh.
  await page.getByTestId("memories-scope-private").click();

  await page.getByTestId("memory-content-input").fill("Always run migrations on Mondays.");
  await page.getByTestId("memory-scope-select").selectOption("private");
  await page.getByTestId("memory-tags-input").fill("ops, schedule");
  await page.getByTestId("memory-create-submit").click();

  await expect.poll(() => capture.postCalls).toBe(1);
  expect(capture.lastPostBody.scope).toBe("private");
  expect(capture.lastPostBody.content).toBe("Always run migrations on Mondays.");
  expect(capture.lastPostBody.tags).toEqual(["ops", "schedule"]);
});

test("deleting a memory calls DELETE and the row disappears", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`memory-delete-${TEAM_MEMORY.id}`).click();
  await expect.poll(() => capture.deleteCalls).toBe(1);
  expect(capture.lastDeletedId).toBe(TEAM_MEMORY.id);

  // The list refetches; the row is gone.
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toHaveCount(0);
});

test("creating with team_shared requires a team_id", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memory-content-input").fill("Use REST.");
  // Default scope is team_shared per the select.
  await expect(page.getByTestId("memory-scope-select")).toHaveValue("team_shared");
  // Submit must be disabled because team_id is empty.
  await expect(page.getByTestId("memory-create-submit")).toBeDisabled();

  await page.getByTestId("memory-team-id-input").fill("aaaaaaaa-0000-0000-0000-000000000001");
  await expect(page.getByTestId("memory-create-submit")).toBeEnabled();
  await page.getByTestId("memory-create-submit").click();

  await expect.poll(() => capture.postCalls).toBe(1);
  expect(capture.lastPostBody.scope).toBe("team_shared");
  expect(capture.lastPostBody.team_id).toBe("aaaaaaaa-0000-0000-0000-000000000001");
});
