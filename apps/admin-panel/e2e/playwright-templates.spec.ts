import { expect, test } from "@playwright/test";

/**
 * E2E for the pre-loaded E2E test templates registry (Plan 09 task_09_15).
 *
 * Plan 09 Fase D ships a curated set of ready-to-use Playwright E2E test
 * templates for the common web flows — login, signup, checkout, search and
 * form-submit. Each template is a parametrized, well-formed Playwright spec
 * skeleton (URLs + selectors are `{{parameters}}`, not hard-coded) that the QA
 * E2E Automator (task_09_14) or a user instantiates against their own app.
 *
 * The canonical, validated registry lives in the backend
 * (`apps/api-server/src/api_server/marketplace/e2e_templates.py`, covered by
 * `tests/integration/test_e2e_test_templates.py`). This spec is the *front
 * line* proof that the instantiated skeletons are real, runnable Playwright
 * specs: it mirrors the registry's substitution contract, instantiates each
 * flagship flow with concrete selectors/URLs, and asserts the rendered spec is
 * marker-free and structurally a Playwright test. It runs fully offline — no
 * admin-panel server, no backend — so it stays a self-contained skeleton check.
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_09_15 — it is marked
 * PENDING HUMAN VERIFICATION (the node-playwright runtime needs a browser this
 * environment does not provide). Run it with
 * `npx playwright test e2e/playwright-templates.spec.ts`.
 */

interface TemplateParameter {
  name: string;
  description: string;
  example: string;
  default?: string;
}

interface E2ETemplate {
  name: string;
  description: string;
  version: string;
  flow: string;
  parameters: TemplateParameter[];
  body: string;
}

// ---------------------------------------------------------------------------
// The registry, mirrored from the backend source of truth. Kept in lock-step
// with api_server.marketplace.e2e_templates.BUILTIN_E2E_TEMPLATES.
// ---------------------------------------------------------------------------
const BASE_URL: TemplateParameter = {
  name: "base_url",
  description: "The base URL of the application under test.",
  example: "https://app.example.test",
  default: "http://localhost:3000",
};

const TEMPLATES: Record<string, E2ETemplate> = {
  login: {
    name: "login",
    description: "Log a registered user in and assert the post-login landing.",
    version: "1.0.0",
    flow: "login",
    parameters: [
      BASE_URL,
      {
        name: "login_path",
        description: "Path to the login page.",
        example: "/login",
        default: "/login",
      },
      {
        name: "email_selector",
        description: "Selector for the email field.",
        example: "input[name='email']",
      },
      {
        name: "email_value",
        description: "The email to log in with.",
        example: "user@example.test",
      },
      {
        name: "password_selector",
        description: "Selector for the password field.",
        example: "input[name='password']",
      },
      { name: "password_value", description: "The password to log in with.", example: "s3cr3t!" },
      {
        name: "submit_selector",
        description: "Selector for the submit button.",
        example: "button[type='submit']",
      },
      {
        name: "success_url_pattern",
        description: "Regex the URL should match after login.",
        example: "/dashboard",
        default: "/dashboard",
      },
      {
        name: "success_selector",
        description: "Selector visible only when logged in.",
        example: "[data-testid='user-menu']",
      },
    ],
    body: [
      'import { expect, test } from "@playwright/test";',
      "",
      'test("a registered user can log in", async ({ page }) => {',
      '  await page.goto("{{base_url}}{{login_path}}");',
      '  await page.fill("{{email_selector}}", "{{email_value}}");',
      '  await page.fill("{{password_selector}}", "{{password_value}}");',
      '  await page.click("{{submit_selector}}");',
      '  await expect(page).toHaveURL(new RegExp("{{success_url_pattern}}"));',
      '  await expect(page.locator("{{success_selector}}")).toBeVisible();',
      "});",
      "",
    ].join("\n"),
  },
  signup: {
    name: "signup",
    description: "Register a brand-new account and assert the welcome state.",
    version: "1.0.0",
    flow: "signup",
    parameters: [
      BASE_URL,
      {
        name: "signup_path",
        description: "Path to the signup page.",
        example: "/signup",
        default: "/signup",
      },
      {
        name: "email_selector",
        description: "Selector for the email field.",
        example: "input[name='email']",
      },
      {
        name: "email_value",
        description: "A unique email to register.",
        example: "new-user@example.test",
      },
      {
        name: "password_selector",
        description: "Selector for the password field.",
        example: "input[name='password']",
      },
      { name: "password_value", description: "The password to register with.", example: "s3cr3t!" },
      {
        name: "confirm_selector",
        description: "Selector for confirm-password.",
        example: "input[name='confirm']",
      },
      {
        name: "submit_selector",
        description: "Selector for the create button.",
        example: "button[type='submit']",
      },
      {
        name: "success_selector",
        description: "Selector shown after signup.",
        example: "[data-testid='welcome']",
      },
    ],
    body: [
      'import { expect, test } from "@playwright/test";',
      "",
      'test("a new user can sign up", async ({ page }) => {',
      '  await page.goto("{{base_url}}{{signup_path}}");',
      '  await page.fill("{{email_selector}}", "{{email_value}}");',
      '  await page.fill("{{password_selector}}", "{{password_value}}");',
      '  await page.fill("{{confirm_selector}}", "{{password_value}}");',
      '  await page.click("{{submit_selector}}");',
      '  await expect(page.locator("{{success_selector}}")).toBeVisible();',
      "});",
      "",
    ].join("\n"),
  },
  checkout: {
    name: "checkout",
    description: "Add a product to the cart and complete a checkout/payment.",
    version: "1.0.0",
    flow: "checkout",
    parameters: [
      BASE_URL,
      {
        name: "product_path",
        description: "Path to a product page.",
        example: "/products/1",
        default: "/products/1",
      },
      {
        name: "add_to_cart_selector",
        description: "Selector for add-to-cart.",
        example: "[data-testid='add-to-cart']",
      },
      {
        name: "cart_path",
        description: "Path to the cart page.",
        example: "/cart",
        default: "/cart",
      },
      {
        name: "checkout_selector",
        description: "Selector for proceed-to-checkout.",
        example: "[data-testid='checkout']",
      },
      {
        name: "card_selector",
        description: "Selector for the card field.",
        example: "input[name='card']",
      },
      { name: "card_value", description: "A (test) card number.", example: "4242424242424242" },
      {
        name: "pay_selector",
        description: "Selector for the pay button.",
        example: "[data-testid='pay']",
      },
      {
        name: "confirmation_selector",
        description: "Selector for order confirmation.",
        example: "[data-testid='order-confirmed']",
      },
    ],
    body: [
      'import { expect, test } from "@playwright/test";',
      "",
      'test("a shopper can complete checkout", async ({ page }) => {',
      '  await page.goto("{{base_url}}{{product_path}}");',
      '  await page.click("{{add_to_cart_selector}}");',
      '  await page.goto("{{base_url}}{{cart_path}}");',
      '  await page.click("{{checkout_selector}}");',
      '  await page.fill("{{card_selector}}", "{{card_value}}");',
      '  await page.click("{{pay_selector}}");',
      '  await expect(page.locator("{{confirmation_selector}}")).toBeVisible();',
      "});",
      "",
    ].join("\n"),
  },
  search: {
    name: "search",
    description: "Run a search query and assert matching results render.",
    version: "1.0.0",
    flow: "search",
    parameters: [
      BASE_URL,
      {
        name: "search_path",
        description: "Path to the search page.",
        example: "/search",
        default: "/search",
      },
      {
        name: "search_selector",
        description: "Selector for the search input.",
        example: "input[type='search']",
      },
      { name: "query_value", description: "The search query to type.", example: "laptop" },
      {
        name: "results_selector",
        description: "Selector for the results container.",
        example: "[data-testid='results']",
      },
      { name: "expected_text", description: "Text the results should contain.", example: "laptop" },
    ],
    body: [
      'import { expect, test } from "@playwright/test";',
      "",
      'test("a user can search and see results", async ({ page }) => {',
      '  await page.goto("{{base_url}}{{search_path}}");',
      '  await page.fill("{{search_selector}}", "{{query_value}}");',
      '  await page.press("{{search_selector}}", "Enter");',
      '  await expect(page.locator("{{results_selector}}")).toBeVisible();',
      '  await expect(page.locator("{{results_selector}}")).toContainText("{{expected_text}}");',
      "});",
      "",
    ].join("\n"),
  },
  "form-submit": {
    name: "form-submit",
    description: "Fill and submit a generic form, asserting the success message.",
    version: "1.0.0",
    flow: "form-submit",
    parameters: [
      BASE_URL,
      {
        name: "form_path",
        description: "Path to the form page.",
        example: "/contact",
        default: "/contact",
      },
      {
        name: "name_selector",
        description: "Selector for the name field.",
        example: "input[name='name']",
      },
      { name: "name_value", description: "The name to enter.", example: "Ada Lovelace" },
      {
        name: "message_selector",
        description: "Selector for the message field.",
        example: "textarea[name='message']",
      },
      { name: "message_value", description: "The message to enter.", example: "Hello there!" },
      {
        name: "submit_selector",
        description: "Selector for the submit button.",
        example: "button[type='submit']",
      },
      {
        name: "success_selector",
        description: "Selector for the success element.",
        example: "[data-testid='form-success']",
      },
    ],
    body: [
      'import { expect, test } from "@playwright/test";',
      "",
      'test("a user can submit a form and see confirmation", async ({ page }) => {',
      '  await page.goto("{{base_url}}{{form_path}}");',
      '  await page.fill("{{name_selector}}", "{{name_value}}");',
      '  await page.fill("{{message_selector}}", "{{message_value}}");',
      '  await page.click("{{submit_selector}}");',
      '  await expect(page.locator("{{success_selector}}")).toBeVisible();',
      "});",
      "",
    ].join("\n"),
  },
};

const PLACEHOLDER_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

function placeholders(body: string): Set<string> {
  const out = new Set<string>();
  for (const match of body.matchAll(PLACEHOLDER_RE)) {
    out.add(match[1]);
  }
  return out;
}

/** Mirror of E2ETestTemplate.instantiate — required params must be supplied. */
function instantiate(template: E2ETemplate, values: Record<string, string>): string {
  const declared = new Map(template.parameters.map((p) => [p.name, p]));
  for (const key of Object.keys(values)) {
    if (!declared.has(key)) {
      throw new Error(`unknown parameter '${key}'`);
    }
  }
  const resolved = new Map<string, string>();
  for (const [name, param] of declared) {
    if (name in values) {
      resolved.set(name, values[name]);
    } else if (param.default !== undefined) {
      resolved.set(name, param.default);
    } else {
      throw new Error(`missing required parameter '${name}'`);
    }
  }
  return template.body.replace(PLACEHOLDER_RE, (_full, name: string) => resolved.get(name) ?? "");
}

/** A concrete value for every parameter of a template (example-driven). */
function exampleValues(template: E2ETemplate): Record<string, string> {
  const values: Record<string, string> = {};
  for (const param of template.parameters) {
    values[param.name] = param.example;
  }
  return values;
}

// ---------------------------------------------------------------------------
// Registry shape
// ---------------------------------------------------------------------------
test("the registry ships the flagship flows (login/signup/checkout) + search/form-submit", () => {
  expect(Object.keys(TEMPLATES).sort()).toEqual(
    ["checkout", "form-submit", "login", "search", "signup"].sort(),
  );
});

test("every template declares uniquely-named parameters that match its placeholders", () => {
  for (const template of Object.values(TEMPLATES)) {
    expect(template.parameters.length).toBeGreaterThan(0);
    const names = template.parameters.map((p) => p.name);
    expect(new Set(names).size).toBe(names.length);
    // Declared == referenced: no dead knob, no undeclared substitution.
    expect(placeholders(template.body)).toEqual(new Set(names));
    expect(template.version).toMatch(/^\d+\.\d+\.\d+$/);
  }
});

// ---------------------------------------------------------------------------
// Instantiation
// ---------------------------------------------------------------------------
test("each template instantiates into a marker-free, well-formed Playwright spec", () => {
  for (const template of Object.values(TEMPLATES)) {
    const rendered = instantiate(template, exampleValues(template));
    expect(rendered).not.toMatch(PLACEHOLDER_RE);
    expect(rendered).toContain("@playwright/test");
    expect(rendered).toContain("test(");
    expect(rendered).toContain("expect(");
  }
});

test("optional parameters fall back to their defaults", () => {
  const login = TEMPLATES.login;
  const required: Record<string, string> = {};
  for (const param of login.parameters) {
    if (param.default === undefined) {
      required[param.name] = param.example;
    }
  }
  const rendered = instantiate(login, required);
  expect(rendered).not.toMatch(PLACEHOLDER_RE);
  // base_url default lands in the output.
  expect(rendered).toContain("http://localhost:3000");
});

test("a missing required parameter throws", () => {
  expect(() => instantiate(TEMPLATES.login, { base_url: "https://app.example.test" })).toThrow(
    /missing required parameter/,
  );
});

test("an unknown parameter throws", () => {
  expect(() => instantiate(TEMPLATES.search, { not_a_real_param: "x" })).toThrow(
    /unknown parameter/,
  );
});
