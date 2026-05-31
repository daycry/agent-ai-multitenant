import { expect, test } from "@playwright/test";

/**
 * E2E for the public developer portal (Plan 15, task_15_25).
 *
 * The portal lives at /developers — a public route group OUTSIDE the
 * auth-gated /admin segment, and it makes no API calls. So these tests
 * need NO login and NO live backend stack: only `npm run dev` (the
 * Playwright webServer auto-starts it). That keeps the spec runnable in
 * a frontend-only environment.
 *
 * WRITTEN, NOT RUN here (Plan 15 task brief): the file-exists / build
 * gates are what task_15_25 verifies in this pass; this spec is the
 * `auto_15_25_a` signal that runs later with a live frontend.
 */

test.describe("developer portal", () => {
  test("landing surfaces the four entry cards + canonical docs", async ({ page }) => {
    await page.goto("/developers", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("dev-portal")).toBeVisible();
    await expect(page.getByTestId("dev-portal-intro")).toBeVisible();

    await expect(page.getByTestId("dev-portal-card-api-reference")).toBeVisible();
    await expect(page.getByTestId("dev-portal-card-sdks")).toBeVisible();
    await expect(page.getByTestId("dev-portal-card-tutorials")).toBeVisible();
    await expect(page.getByTestId("dev-portal-card-webhooks")).toBeVisible();

    await expect(page.getByTestId("dev-portal-canonical-docs")).toBeVisible();
  });

  test("api-reference page links the public OpenAPI + Swagger UI", async ({ page }) => {
    await page.goto("/developers/api-reference", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("api-reference-intro")).toBeVisible();
    await expect(page.getByTestId("api-reference-openapi-json")).toHaveAttribute(
      "href",
      "/api/v1/openapi.json",
    );
    await expect(page.getByTestId("api-reference-swagger")).toHaveAttribute("href", "/api/v1/docs");
    await expect(page.getByTestId("api-reference-endpoints")).toContainText("/api/v1/projects");
    await expect(page.getByTestId("api-reference-status")).toContainText("429");
  });

  test("sdks page shows Python + TypeScript install + quickstart", async ({ page }) => {
    await page.goto("/developers/sdks", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("sdks-python")).toContainText("agentic-platform-sdk");
    await expect(page.getByTestId("sdks-python")).toContainText("pip install");
    await expect(page.getByTestId("sdks-typescript")).toContainText("@agentic-platform/sdk");
    await expect(page.getByTestId("sdks-typescript")).toContainText("npm install");
  });

  test("tutorials page walks mint-token → call-api → webhook", async ({ page }) => {
    await page.goto("/developers/tutorials", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("tutorials-mint")).toContainText("/auth/api-tokens");
    await expect(page.getByTestId("tutorials-call")).toContainText("X-API-Token");
    await expect(page.getByTestId("tutorials-webhook")).toContainText("incoming-webhooks");
  });

  test("webhooks page documents origins + fail-closed check order", async ({ page }) => {
    await page.goto("/developers/webhooks", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("webhooks-origins")).toContainText("github");
    await expect(page.getByTestId("webhooks-origins")).toContainText("generic");
    await expect(page.getByTestId("webhooks-checks")).toContainText("Verificar HMAC");
  });

  test("top nav navigates between portal sections", async ({ page }) => {
    await page.goto("/developers", { waitUntil: "domcontentloaded" });

    await page.getByTestId("dev-portal-nav-sdks").click();
    await expect(page).toHaveURL(/\/developers\/sdks$/);

    await page.getByTestId("dev-portal-nav-webhooks").click();
    await expect(page).toHaveURL(/\/developers\/webhooks$/);
  });
});
