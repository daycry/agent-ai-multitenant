import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/memories (Plan 04 task_04_06).
 *
 * Mocks GET/POST/DELETE /memories. Drives:
 *   - rendering the list with the default scope filter ("all"),
 *   - flipping the scope filter,
 *   - creating a private memory via the form,
 *   - deleting one of the listed memories.
 *
 * Reparado el 2026-08-19 (subset mockeado de CI), dos derivas:
 *
 *   1. **El filtro por defecto es "all", no `team_shared`**, desde `8c2be848`
 *      ("admin UI polish"): la pantalla abre enseñándolo todo y el operador
 *      filtra si quiere. El test seguía afirmando el default de `fb34525e`.
 *      Ahora afirma el actual Y que con él se ven memorias de MÁS de un scope,
 *      que es lo que "all" significa — antes bastaba con que se viera una.
 *   2. **El contenido se escribe en un `<MarkdownTextarea>`**, cuyo
 *      `data-testid` nombra el contenedor: el `<textarea>` es `-edit`.
 *   2bis. Y una carrera de HIDRATACIÓN: los primeros pasos de varios tests
 *      (rellenar, comprobar un `<select>` con su valor por defecto, ver el botón
 *      deshabilitado) pasan igual sobre el HTML SERVIDO, así que el primer click
 *      podía caer antes de que React enganchara sus handlers y perderse en
 *      silencio. `gotoMemories()` espera a que la lista —que sale de la query
 *      mockeada— esté pintada: eso sólo ocurre con el cliente ya vivo.
 *
 *   3. **El equipo ya no se teclea**: es un `<TeamCombobox>` que busca contra
 *      `GET /teams`. Escribir un UUID a mano dejó de ser la interacción real
 *      (y era, de hecho, la peor parte de la UX que el combobox vino a
 *      arreglar).
 */

// Shared shape so memories with different owner pointers
// (user_id vs team_id vs project_id) live in the same list without
// fighting the type checker.
interface MemoryFixture {
  id: string;
  tenant_id: string;
  scope: string;
  type: string;
  content: string;
  tags: string[];
  user_id: string | null;
  team_id: string | null;
  project_id: string | null;
  source_execution_id: string | null;
  agent_id: string | null;
  has_embedding: boolean;
  created_at: string;
  updated_at: string;
}

/** El equipo dueño de la memoria compartida (y la única fila de `/teams`). */
const TEAM_ID = "aaaaaaaa-0000-0000-0000-000000000001";

const TEAM_MEMORY: MemoryFixture = {
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

const PRIVATE_MEMORY: MemoryFixture = {
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
  const store: Record<string, MemoryFixture[]> = {
    team_shared: [TEAM_MEMORY],
    private: [PRIVATE_MEMORY],
    project_shared: [],
    global: [],
  };

  await seedSession(page);

  // El selector de equipo es un `<TeamCombobox>`: busca contra `GET /teams`.
  await page.route(/http:\/\/localhost:8001\/teams(\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: TEAM_ID, name: "Equipo A" }]),
    }),
  );

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
      const created: MemoryFixture = {
        ...PRIVATE_MEMORY,
        id: `33333333-3333-3333-3333-${String(capture.postCalls).padStart(12, "0")}`,
        scope: String(body.scope ?? "private"),
        type: String(body.type ?? "semantic"),
        content: String(body.content ?? ""),
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

/**
 * Navega y espera a que el cliente esté vivo (ver punto 2bis de la cabecera).
 */
async function gotoMemories(page: Page): Promise<void> {
  await page.goto("/admin/memories", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toBeVisible();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
test("el filtro por defecto es 'all' y lista memorias de todos los scopes", async ({ page }) => {
  await setup(page);
  await gotoMemories(page);

  await expect(page.getByTestId("memories-page")).toBeVisible();
  await expect(page.getByTestId("memories-scope-all")).toHaveAttribute("aria-pressed", "true");
  // "all" no es "la de mi equipo": conviven scopes distintos en la misma lista.
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toBeVisible();
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toContainText("REST");
  await expect(page.getByTestId(`memory-${PRIVATE_MEMORY.id}`)).toBeVisible();
  await expect(page.getByTestId(`memory-${PRIVATE_MEMORY.id}`)).toContainText("Friday");
});

test("changing scope filter to private surfaces the private memory", async ({ page }) => {
  await setup(page);
  await gotoMemories(page);

  await page.getByTestId("memories-scope-private").click();
  await expect(page.getByTestId(`memory-${PRIVATE_MEMORY.id}`)).toBeVisible();
  // Team memory must not show under the private filter.
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toHaveCount(0);
});

test("creating a private memory POSTs and refreshes the list", async ({ page }) => {
  const capture = await setup(page);
  await gotoMemories(page);

  // Switch to private so we can see the new row after the refresh.
  await page.getByTestId("memories-scope-private").click();

  await page.getByTestId("memory-content-input-edit").fill("Always run migrations on Mondays.");
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
  await gotoMemories(page);

  await page.getByTestId(`memory-delete-${TEAM_MEMORY.id}`).click();
  await expect.poll(() => capture.deleteCalls).toBe(1);
  expect(capture.lastDeletedId).toBe(TEAM_MEMORY.id);

  // The list refetches; the row is gone.
  await expect(page.getByTestId(`memory-${TEAM_MEMORY.id}`)).toHaveCount(0);
});

test("creating with team_shared requires a team_id", async ({ page }) => {
  const capture = await setup(page);
  await gotoMemories(page);

  await page.getByTestId("memory-content-input-edit").fill("Use REST.");
  // Default scope is team_shared per the select.
  await expect(page.getByTestId("memory-scope-select")).toHaveValue("team_shared");
  // Submit must be disabled because team_id is empty.
  await expect(page.getByTestId("memory-create-submit")).toBeDisabled();

  await page.getByTestId("memory-team-id-input-trigger").click();
  await page.getByTestId(`memory-team-id-input-option-${TEAM_ID}`).click();
  await expect(page.getByTestId("memory-create-submit")).toBeEnabled();
  await page.getByTestId("memory-create-submit").click();

  await expect.poll(() => capture.postCalls).toBe(1);
  expect(capture.lastPostBody.scope).toBe("team_shared");
  expect(capture.lastPostBody.team_id).toBe(TEAM_ID);
});
