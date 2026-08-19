import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/projects/{id}/mcp-servers (Plan 05 task_05_06).
 *
 * Mocks:
 *   - GET /projects/{id}        — returns the project + its mcp_servers
 *   - PUT /projects/{id}        — captures every save so the assertions
 *                                  can inspect what got persisted
 *
 * Drives:
 *   - empty state when the project has no MCP servers,
 *   - rendering of one stdio + one http server card,
 *   - add: dialog opens → fill → submit → PUT called with the new entry,
 *   - edit: pencil → dialog pre-filled → change name → PUT updates,
 *   - delete: trash → confirm() accepted → PUT with the entry removed,
 *   - transport switch: changing transport hides/shows the right fields.
 */

const PROJECT_ID = "11111111-0000-0000-0000-000000000001";

interface McpServerFixture {
  name: string;
  transport: "stdio" | "sse" | "streamable_http";
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  auth_ref: string | null;
  timeout_s: number;
}

const STDIO_SERVER: McpServerFixture = {
  name: "docling",
  transport: "stdio",
  command: "docling-mcp",
  args: ["--transport", "stdio"],
  env: {},
  url: null,
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

const HTTP_SERVER: McpServerFixture = {
  name: "github",
  transport: "streamable_http",
  command: null,
  args: [],
  env: {},
  url: "https://github-mcp.example/mcp",
  headers: {},
  auth_ref: "vault:secret/data/mcp/github/proj-42",
  timeout_s: 30,
};

interface Capture {
  putCalls: number;
  lastPutBody: { mcp_servers?: McpServerFixture[] } | null;
}

async function setup(page: Page, initialServers: McpServerFixture[]): Promise<Capture> {
  const capture: Capture = { putCalls: 0, lastPutBody: null };
  let servers = [...initialServers];

  await seedSession(page);

  await page.route(`http://localhost:8001/projects/${PROJECT_ID}`, (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: PROJECT_ID,
          name: "Mediapro Internal",
          mcp_servers: servers,
        }),
      });
    }
    if (method === "PUT") {
      capture.putCalls += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as {
        mcp_servers?: McpServerFixture[];
      };
      capture.lastPutBody = body;
      if (body.mcp_servers) {
        servers = body.mcp_servers;
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: PROJECT_ID,
          name: "Mediapro Internal",
          mcp_servers: servers,
        }),
      });
    }
    return route.continue();
  });

  return capture;
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------
test("empty project shows the no-servers message", async ({ page }) => {
  await setup(page, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("project-mcp-page")).toBeVisible();
  await expect(page.getByTestId("project-mcp-empty")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
test("project with servers renders a card per entry with the right badges", async ({ page }) => {
  await setup(page, [STDIO_SERVER, HTTP_SERVER]);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId(`mcp-server-card-${STDIO_SERVER.name}`)).toBeVisible();
  await expect(page.getByTestId(`mcp-server-card-${HTTP_SERVER.name}`)).toBeVisible();

  // stdio server shows command + args; http server shows the URL.
  await expect(page.getByTestId(`mcp-server-card-${STDIO_SERVER.name}`)).toContainText(
    "docling-mcp",
  );
  await expect(page.getByTestId(`mcp-server-card-${HTTP_SERVER.name}`)).toContainText(
    "https://github-mcp.example/mcp",
  );

  // Only the http server has auth_ref, so only its card surfaces the
  // "vault" badge.
  await expect(page.getByTestId(`mcp-server-auth-${HTTP_SERVER.name}`)).toBeVisible();
  await expect(page.getByTestId(`mcp-server-auth-${STDIO_SERVER.name}`)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Add
// ---------------------------------------------------------------------------
test("adding a stdio server PUTs the new entry appended to mcp_servers", async ({ page }) => {
  const capture = await setup(page, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("mcp-add-button").click();
  await expect(page.getByTestId("mcp-server-dialog")).toBeVisible();

  await page.getByTestId("mcp-form-name").fill("toy-stdio");
  // stdio is the default — no transport switch needed
  await page.getByTestId("mcp-form-command").fill("toy-mcp");
  await page.getByTestId("mcp-form-args").fill("--transport\nstdio");
  await page.getByTestId("mcp-form-submit").click();

  await expect.poll(() => capture.putCalls).toBe(1);
  const payload = capture.lastPutBody?.mcp_servers ?? [];
  expect(payload).toHaveLength(1);
  expect(payload[0]).toMatchObject({
    name: "toy-stdio",
    transport: "stdio",
    command: "toy-mcp",
    args: ["--transport", "stdio"],
    url: null,
    auth_ref: null,
  });

  await expect(page.getByTestId("mcp-server-dialog")).toBeHidden();
});

test("adding an http server hides stdio fields and PUTs url + headers", async ({ page }) => {
  const capture = await setup(page, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("mcp-add-button").click();
  await page.getByTestId("mcp-form-name").fill("toy-http");
  await page.getByTestId("mcp-form-transport").selectOption("streamable_http");

  // After the switch, command/args/env are gone; url/headers show up.
  await expect(page.getByTestId("mcp-form-command")).toHaveCount(0);
  await expect(page.getByTestId("mcp-form-args")).toHaveCount(0);
  await expect(page.getByTestId("mcp-form-url")).toBeVisible();

  await page.getByTestId("mcp-form-url").fill("https://toy.example/mcp");
  // La credencial vive bajo "Opciones avanzadas", que arranca PLEGADA: el
  // formulario deja arriba lo que casi siempre basta. El spec la rellenaba
  // directamente, sobre un campo que no estaba en el DOM (2026-08-19).
  await expect(page.getByTestId("mcp-form-auth-ref")).toHaveCount(0);
  await page.getByTestId("mcp-form-advanced-toggle").click();
  await page.getByTestId("mcp-form-auth-ref").fill("vault:secret/data/toy/proj-1");
  await page.getByTestId("mcp-form-submit").click();

  await expect.poll(() => capture.putCalls).toBe(1);
  const payload = capture.lastPutBody?.mcp_servers ?? [];
  expect(payload[0]).toMatchObject({
    name: "toy-http",
    transport: "streamable_http",
    url: "https://toy.example/mcp",
    auth_ref: "vault:secret/data/toy/proj-1",
    command: null,
    args: [],
  });
});

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------
test("editing a server pre-fills the dialog and PUTs the merged array", async ({ page }) => {
  const capture = await setup(page, [STDIO_SERVER]);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId(`mcp-server-edit-${STDIO_SERVER.name}`).click();
  await expect(page.getByTestId("mcp-server-dialog")).toBeVisible();
  await expect(page.getByTestId("mcp-form-name")).toHaveValue(STDIO_SERVER.name);
  await expect(page.getByTestId("mcp-form-command")).toHaveValue(STDIO_SERVER.command ?? "");

  // Rename and resubmit.
  await page.getByTestId("mcp-form-name").fill("docling-renamed");
  await page.getByTestId("mcp-form-submit").click();

  await expect.poll(() => capture.putCalls).toBe(1);
  const payload = capture.lastPutBody?.mcp_servers ?? [];
  expect(payload).toHaveLength(1);
  expect(payload[0]?.name).toBe("docling-renamed");
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
test("deleting a server PUTs the array without that entry", async ({ page }) => {
  const capture = await setup(page, [STDIO_SERVER, HTTP_SERVER]);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  // window.confirm() is used by the page — auto-accept it.
  page.on("dialog", (dialog) => {
    dialog.accept().catch(() => {});
  });

  await page.getByTestId(`mcp-server-delete-${STDIO_SERVER.name}`).click();

  await expect.poll(() => capture.putCalls).toBe(1);
  const payload = capture.lastPutBody?.mcp_servers ?? [];
  expect(payload.map((s) => s.name)).toEqual([HTTP_SERVER.name]);

  await expect(page.getByTestId(`mcp-server-card-${STDIO_SERVER.name}`)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Env editor row add/remove
// ---------------------------------------------------------------------------
test("env editor add/remove rows shows up in the PUT payload", async ({ page }) => {
  const capture = await setup(page, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("mcp-add-button").click();
  await page.getByTestId("mcp-form-name").fill("env-toy");
  await page.getByTestId("mcp-form-command").fill("env-mcp");

  // Add one env row, rename its key, fill the value.
  await page.getByTestId("mcp-form-env-add").click();
  await page.getByTestId("mcp-form-env-key-0").fill("DEBUG");
  await page.getByTestId("mcp-form-env-value-0").fill("1");

  await page.getByTestId("mcp-form-submit").click();
  await expect.poll(() => capture.putCalls).toBe(1);
  expect(capture.lastPutBody?.mcp_servers?.[0]?.env).toEqual({ DEBUG: "1" });
});
