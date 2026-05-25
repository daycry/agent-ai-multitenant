import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the tenant hourly-rate admin screen (Plan 03 task_03_26).
 */

interface PutCapture {
  calls: number;
  lastBody: { hourly_rate?: string | null; hourly_rate_currency?: string | null };
}

async function setup(
  page: Page,
  initial: { hourly_rate: string | null; hourly_rate_currency: string | null } = {
    hourly_rate: null,
    hourly_rate_currency: null,
  },
): Promise<PutCapture> {
  const capture: PutCapture = { calls: 0, lastBody: {} };
  let stored = { ...initial };

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("http://localhost:8001/tenant-settings/hourly-rate", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(stored),
      });
    }
    if (route.request().method() === "PUT") {
      capture.calls += 1;
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.lastBody = body;
      stored = {
        hourly_rate: body.hourly_rate ?? null,
        hourly_rate_currency: (body.hourly_rate_currency ?? "").toUpperCase() || null,
      };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(stored),
      });
    }
    return route.continue();
  });
  return capture;
}

test("page loads with the empty form when no rate is configured", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings/hourly-rate", {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("hourly-rate-form")).toBeVisible();
  await expect(page.getByTestId("hourly-rate-input")).toHaveValue("");
  // Default currency placeholder.
  await expect(page.getByTestId("hourly-rate-currency-input")).toHaveValue("EUR");
  // Submit disabled because no edit has happened yet.
  await expect(page.getByTestId("hourly-rate-submit")).toBeDisabled();
});

test("page seeds the form when the tenant already has a configured rate", async ({ page }) => {
  await setup(page, { hourly_rate: "75.50", hourly_rate_currency: "EUR" });
  await page.goto("/admin/settings/hourly-rate", {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("hourly-rate-input")).toHaveValue("75.50");
  await expect(page.getByTestId("hourly-rate-currency-input")).toHaveValue("EUR");
});

test("editing and saving PUTs the new rate", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/settings/hourly-rate", {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("hourly-rate-input").fill("100");
  await page.getByTestId("hourly-rate-currency-input").fill("usd");
  // After editing, submit becomes enabled.
  const submit = page.getByTestId("hourly-rate-submit");
  await expect(submit).toBeEnabled();
  await submit.click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.hourly_rate).toBe("100");
  expect(capture.lastBody.hourly_rate_currency).toBe("USD");

  // Success line appears.
  await expect(page.getByTestId("hourly-rate-saved")).toBeVisible();
});

test("currency is uppercased while typing", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings/hourly-rate", {
    waitUntil: "domcontentloaded",
  });
  const input = page.getByTestId("hourly-rate-currency-input");
  await input.fill("usd");
  await expect(input).toHaveValue("USD");
});
