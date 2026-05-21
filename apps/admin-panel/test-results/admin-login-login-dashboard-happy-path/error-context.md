# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: admin-login.spec.ts >> login + dashboard happy path
- Location: e2e\admin-login.spec.ts:18:5

# Error details

```
Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3000/login
Call log:
  - navigating to "http://localhost:3000/login", waiting until "load"

```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  |
  3  | /**
  4  |  * Phase-0 happy path: login as a pre-seeded System Admin, land on the
  5  |  * dashboard, see at least the postgres service card.
  6  |  *
  7  |  * Pre-conditions (caller's responsibility):
  8  |  *   - docker compose stack is up.
  9  |  *   - api-server is running on http://localhost:8001 with CORS allowing
  10 |  *     http://localhost:3000.
  11 |  *   - There is a User row with is_system_admin=true matching the
  12 |  *     E2E_ADMIN_* env vars (defaults: root@example.com / longenoughpw).
  13 |  *   - admin-panel dev server is running on http://localhost:3000.
  14 |  */
  15 | const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
  16 | const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";
  17 |
  18 | test("login + dashboard happy path", async ({ page }) => {
> 19 |   await page.goto("/login");
     |              ^ Error: page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3000/login
  20 |
  21 |   await page.getByLabel("Email").fill(ADMIN_EMAIL);
  22 |   await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  23 |   await page.getByRole("button", { name: /sign in/i }).click();
  24 |
  25 |   await expect(page).toHaveURL(/\/admin\/dashboard$/);
  26 |   await expect(page.getByTestId("services-grid")).toBeVisible();
  27 |   await expect(page.getByTestId("service-postgres")).toBeVisible();
  28 | });
  29 |
  30 | test("wrong password shows inline error", async ({ page }) => {
  31 |   await page.goto("/login");
  32 |
  33 |   await page.getByLabel("Email").fill(ADMIN_EMAIL);
  34 |   await page.getByLabel("Password").fill("definitely-wrong");
  35 |   await page.getByRole("button", { name: /sign in/i }).click();
  36 |
  37 |   await expect(page.getByTestId("login-error")).toBeVisible();
  38 |   await expect(page).toHaveURL(/\/login$/);
  39 | });
  40 |
```
