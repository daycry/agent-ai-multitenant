import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the similar-memories dialog (Plan 06.7 task_06_7_08).
 */

const MEMORIES_FIXTURE = [
  {
    id: "mem-target-1",
    tenant_id: "t",
    scope: "team_shared",
    type: "semantic",
    content: "psycopg3 falla con asyncio en proyecto X",
    tags: ["psycopg3", "asyncio"],
    user_id: null,
    team_id: "team-1",
    project_id: null,
    source_execution_id: null,
    agent_id: null,
    has_embedding: true,
    created_at: "2026-05-27T10:00:00Z",
    updated_at: "2026-05-27T10:00:00Z",
  },
];

const SIMILAR_FIXTURE = [
  {
    memory: {
      id: "mem-candidate-1",
      tenant_id: "t",
      scope: "team_shared",
      type: "semantic",
      content: "psycopg3 no funciona bien con asyncio",
      tags: ["psycopg3"],
      user_id: null,
      team_id: "team-1",
      project_id: null,
      source_execution_id: null,
      agent_id: null,
      has_embedding: true,
      created_at: "2026-05-27T10:30:00Z",
      updated_at: "2026-05-27T10:30:00Z",
    },
    similarity: 0.9123,
  },
];

async function setup(
  page: Page,
  opts: {
    similar?: object[];
    onMerge?: (sourceId: string, body: object) => void;
    onDiscard?: (id: string) => void;
  } = {},
): Promise<void> {
  await seedSession(page);
  await page.route("**/memories?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MEMORIES_FIXTURE),
    }),
  );
  await page.route("**/memories", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MEMORIES_FIXTURE),
    }),
  );
  await page.route("**/memories/mem-target-1/similar*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(opts.similar ?? SIMILAR_FIXTURE),
    }),
  );
  await page.route("**/memories/mem-candidate-1/merge-into", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onMerge?.("mem-candidate-1", body);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MEMORIES_FIXTURE[0]),
    });
  });
  await page.route("**/memories/mem-candidate-1", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    opts.onDiscard?.("mem-candidate-1");
    return route.fulfill({ status: 204, body: "" });
  });
}

test("similar button shows on memories with embedding", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("memory-similar-mem-target-1")).toBeVisible();
});

test("clicking similar opens dialog with candidates + similarity %", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memory-similar-mem-target-1").click();
  await expect(page.getByTestId("similar-list")).toBeVisible();
  await expect(page.getByTestId("similar-item-mem-candidate-1")).toBeVisible();
  await expect(page.getByTestId("similar-pct-mem-candidate-1")).toContainText("91.2%");
});

test("merge fires POST /merge-into with target_id of current memory", async ({ page }) => {
  const calls: Array<{ sourceId: string; body: object }> = [];
  await setup(page, {
    onMerge: (sourceId, body) => calls.push({ sourceId, body }),
  });
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memory-similar-mem-target-1").click();
  await page.getByTestId("similar-merge-mem-candidate-1").click();
  await page.waitForTimeout(200);

  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    sourceId: "mem-candidate-1",
    body: { target_id: "mem-target-1" },
  });
});

test("discard fires DELETE on candidate", async ({ page }) => {
  let discarded = false;
  await setup(page, { onDiscard: () => (discarded = true) });
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memory-similar-mem-target-1").click();
  await page.getByTestId("similar-discard-mem-candidate-1").click();
  await page.waitForTimeout(200);
  expect(discarded).toBe(true);
});

test("empty similar list shows empty state", async ({ page }) => {
  await setup(page, { similar: [] });
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("memory-similar-mem-target-1").click();
  await expect(page.getByTestId("similar-empty")).toBeVisible();
});
