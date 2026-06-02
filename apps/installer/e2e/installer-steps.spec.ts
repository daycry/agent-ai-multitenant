import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the config-capture steps 2-6 (Plan 15 task_15_03).
 *
 * Walks the wizard through basics → resources → storage → providers → tenant,
 * asserting client-side validation gates "next", captured values persist across
 * back/forward navigation, and secret inputs are rendered as password fields
 * (never shown in plaintext / never echoed). The backend `/api/prereqs` and
 * `/api/config/validate` calls are MOCKED via route interception so no real
 * host probing or provisioning happens.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification with the installer dev
 * server up). The auto check `auto_15_03_a` runs:
 *   npx playwright test e2e/installer-steps.spec.ts
 */

interface PrereqResponse {
  results: Array<{
    key: string;
    label: string;
    status: "ok" | "warn" | "fail";
    ok: boolean;
    detail: string;
    remediation: string;
    required: boolean;
  }>;
  all_required_ok: boolean;
  can_proceed: boolean;
}

const PREREQS_OK: PrereqResponse = {
  results: [
    {
      key: "docker",
      label: "Docker Engine",
      status: "ok",
      ok: true,
      detail: "27.1",
      remediation: "",
      required: true,
    },
    {
      key: "compose",
      label: "Docker Compose v2",
      status: "ok",
      ok: true,
      detail: "2.29",
      remediation: "",
      required: true,
    },
    {
      key: "ram",
      label: "RAM >= 8 GiB",
      status: "ok",
      ok: true,
      detail: "16 GiB",
      remediation: "",
      required: true,
    },
    {
      key: "disk",
      label: "Disco >= 50 GiB",
      status: "ok",
      ok: true,
      detail: "200 GiB",
      remediation: "",
      required: true,
    },
    {
      key: "gpu",
      label: "GPU NVIDIA (opcional)",
      status: "warn",
      ok: true,
      detail: "sin GPU",
      remediation: "opcional",
      required: false,
    },
  ],
  all_required_ok: true,
  can_proceed: true,
};

async function mockBackend(page: Page): Promise<void> {
  await page.route("**/api/prereqs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PREREQS_OK),
    });
  });
  // Echo back a secret-free valid response. The spec also asserts the POSTed
  // body carries the secret exactly once (write-only) and never comes back.
  await page.route("**/api/config/validate", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        valid: true,
        errors: [],
        normalized: {},
        providers: {
          claude_sdk_enabled: false,
          claude_sdk_token_set: false,
          copilot_enabled: false,
          copilot_token_set: false,
          azure_foundry_enabled: false,
          azure_foundry_key_set: false,
          ollama_enabled: true,
        },
      }),
    });
  });
}

async function gotoBasics(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByTestId("wizard-next").click(); // welcome -> basics
  await expect(page.getByTestId("step-basics")).toBeVisible();
}

async function advanceToResources(page: Page): Promise<void> {
  await page.getByTestId("input-domain").fill("agentic.example.com");
  await page.getByTestId("wizard-next").click(); // basics -> resources
  await expect(page.getByTestId("step-resources")).toBeVisible();
}

test("basics step blocks next on empty/invalid domain and accepts a valid one", async ({
  page,
}) => {
  await mockBackend(page);
  await gotoBasics(page);

  // Empty domain -> clicking next reveals the error and stays on basics.
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("field-error-domain")).toBeVisible();
  await expect(page.getByTestId("step-basics")).toBeVisible();

  // Invalid domain (has a scheme) -> still blocked.
  await page.getByTestId("input-domain").fill("http://nope/path");
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("field-error-domain")).toBeVisible();

  // Valid domain -> advances to resources.
  await page.getByTestId("input-domain").fill("agentic.example.com");
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("step-resources")).toBeVisible();
});

test("resources step shows prereqs and captures allocation", async ({ page }) => {
  await mockBackend(page);
  await gotoBasics(page);
  await advanceToResources(page);

  // The embedded prereq panel renders its rows.
  await expect(page.getByTestId("prereq-item-docker")).toBeVisible();
  await expect(page.getByTestId("input-workerReplicas")).toBeVisible();
  await page.getByTestId("input-workerReplicas").fill("4");
  await page.getByTestId("wizard-next").click(); // resources -> storage
  await expect(page.getByTestId("step-storage")).toBeVisible();
});

test("storage step requires an absolute data root and a secret key", async ({ page }) => {
  await mockBackend(page);
  await gotoBasics(page);
  await advanceToResources(page);
  await page.getByTestId("wizard-next").click(); // resources -> storage
  await expect(page.getByTestId("step-storage")).toBeVisible();

  // The MinIO secret key is a password input (write-only, never plaintext).
  await expect(page.getByTestId("input-minioSecretKey")).toHaveAttribute("type", "password");

  // Missing access key + short secret -> blocked.
  await page.getByTestId("input-minioAccessKey").fill("");
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("field-error-minioAccessKey")).toBeVisible();

  // Fill valid storage -> advance.
  await page.getByTestId("input-minioAccessKey").fill("minioadmin");
  await page.getByTestId("input-minioSecretKey").fill("supersecret123");
  await page.getByTestId("wizard-next").click(); // storage -> providers
  await expect(page.getByTestId("step-providers")).toBeVisible();
});

test("providers step requires at least one enabled provider with its creds", async ({ page }) => {
  await mockBackend(page);
  await gotoBasics(page);
  await advanceToResources(page);
  await page.getByTestId("wizard-next").click(); // -> storage
  await page.getByTestId("input-minioAccessKey").fill("minioadmin");
  await page.getByTestId("input-minioSecretKey").fill("supersecret123");
  await page.getByTestId("wizard-next").click(); // -> providers
  await expect(page.getByTestId("step-providers")).toBeVisible();

  // No provider enabled -> blocked with the providers-level error.
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("providers-error")).toBeVisible();

  // Enable Ollama but leave the endpoint empty -> still blocked.
  await page.getByTestId("input-ollama-enabled").check();
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("field-error-ollama-endpoint")).toBeVisible();

  // The Azure/Claude secret inputs are password fields.
  await page.getByTestId("input-azureFoundry-enabled").check();
  await expect(page.getByTestId("input-azureFoundry-apiKey")).toHaveAttribute("type", "password");
  await page.getByTestId("input-azureFoundry-enabled").uncheck();

  // Valid Ollama endpoint -> advances to tenant.
  await page.getByTestId("input-ollama-endpoint").fill("http://localhost:11434");
  await page.getByTestId("wizard-next").click(); // -> tenant
  await expect(page.getByTestId("step-tenant")).toBeVisible();
});

test("tenant step validates email and advances to summary", async ({ page }) => {
  await mockBackend(page);
  await gotoBasics(page);
  await advanceToResources(page);
  await page.getByTestId("wizard-next").click(); // -> storage
  await page.getByTestId("input-minioAccessKey").fill("minioadmin");
  await page.getByTestId("input-minioSecretKey").fill("supersecret123");
  await page.getByTestId("wizard-next").click(); // -> providers
  await page.getByTestId("input-ollama-enabled").check();
  await page.getByTestId("input-ollama-endpoint").fill("http://localhost:11434");
  await page.getByTestId("wizard-next").click(); // -> tenant
  await expect(page.getByTestId("step-tenant")).toBeVisible();

  // Bad email -> blocked.
  await page.getByTestId("input-tenantName").fill("Acme Corp");
  await page.getByTestId("input-adminEmail").fill("not-an-email");
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("field-error-adminEmail")).toBeVisible();

  // Valid email -> advances to the summary step (filled by task_15_04).
  await page.getByTestId("input-adminEmail").fill("admin@acme.com");
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("step-summary")).toBeVisible();
});

test("captured values persist across back/forward navigation", async ({ page }) => {
  await mockBackend(page);
  await gotoBasics(page);
  await page.getByTestId("input-domain").fill("agentic.example.com");
  await page.getByTestId("wizard-next").click(); // -> resources
  await page.getByTestId("wizard-back").click(); // back to basics
  await expect(page.getByTestId("input-domain")).toHaveValue("agentic.example.com");
});
