import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the summary / confirmation step 7 (Plan 15 task_15_04).
 *
 * Walks the wizard to the summary step and asserts the Phase-A deliverable:
 *   - the captured config is reviewed back, with EVERY secret masked (the MinIO
 *     secret key and provider tokens are never shown in plaintext),
 *   - the resource preview lists services + ports + volumes + estimated
 *     RAM/disk derived from the captured config,
 *   - a confirm gate blocks the "Instalar" button until the operator ticks it,
 *   - going back from the summary un-confirms (re-confirmation required).
 *
 * The backend `/api/prereqs` and `/api/config/validate` calls are MOCKED via
 * route interception — no real host probing or provisioning happens. The
 * preview is computed purely client-side from the in-memory config.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification with the installer dev
 * server up). The auto check `auto_15_04_a` runs:
 *   npx playwright test e2e/installer-summary.spec.ts
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
  await page.route("**/api/config/validate", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ valid: true, errors: [], normalized: {}, providers: null }),
    });
  });
}

/** Walk the whole wizard to the summary step with a known, valid config. */
async function advanceToSummary(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByTestId("wizard-next").click(); // welcome -> basics

  await page.getByTestId("input-domain").fill("agentic.example.com");
  await page.getByTestId("wizard-next").click(); // basics -> resources

  await expect(page.getByTestId("step-resources")).toBeVisible();
  await page.getByTestId("input-workerReplicas").fill("3");
  await page.getByTestId("wizard-next").click(); // resources -> storage

  await page.getByTestId("input-minioAccessKey").fill("minioadmin");
  await page.getByTestId("input-minioSecretKey").fill("supersecret123");
  await page.getByTestId("wizard-next").click(); // storage -> providers

  await page.getByTestId("input-ollama-enabled").check();
  await page.getByTestId("input-ollama-endpoint").fill("http://localhost:11434");
  await page.getByTestId("wizard-next").click(); // providers -> tenant

  await page.getByTestId("input-tenantName").fill("Acme Corp");
  await page.getByTestId("input-adminEmail").fill("admin@acme.com");
  await page.getByTestId("wizard-next").click(); // tenant -> summary

  await expect(page.getByTestId("step-summary")).toBeVisible();
}

test("summary reviews captured config with secrets masked", async ({ page }) => {
  await mockBackend(page);
  await advanceToSummary(page);

  // The captured non-secret values appear in the review.
  await expect(page.getByTestId("summary-group-system")).toContainText("agentic.example.com");
  await expect(page.getByTestId("summary-group-storage")).toContainText("minioadmin");
  await expect(page.getByTestId("summary-group-tenant")).toContainText("admin@acme.com");

  // The MinIO secret key is rendered MASKED, never as the typed plaintext.
  const storageGroup = page.getByTestId("summary-group-storage");
  await expect(storageGroup).not.toContainText("supersecret123");
  await expect(storageGroup).toContainText("••••••••");
});

test("summary previews services, ports, volumes and RAM/disk estimates", async ({ page }) => {
  await mockBackend(page);
  await advanceToSummary(page);

  // Headline estimates.
  await expect(page.getByTestId("estimate-services")).toBeVisible();
  await expect(page.getByTestId("estimate-ram")).toContainText("GiB");
  await expect(page.getByTestId("estimate-disk")).toContainText("GiB");

  // Core infra + app services are listed with their ports.
  await expect(page.getByTestId("service-row-postgres")).toContainText("5432");
  await expect(page.getByTestId("service-row-redis")).toContainText("6379");
  await expect(page.getByTestId("service-row-vault")).toContainText("8200");
  await expect(page.getByTestId("service-row-api-server")).toContainText("8000");

  // The worker tier fans out to the chosen replica count (3 -> worker-3 exists).
  await expect(page.getByTestId("service-row-worker-1")).toBeVisible();
  await expect(page.getByTestId("service-row-worker-3")).toBeVisible();

  // Persistent volumes are previewed.
  await expect(page.getByTestId("summary-volumes")).toContainText("postgres_data");
  await expect(page.getByTestId("summary-volumes")).toContainText("minio_data");
});

test("confirm gate blocks Install until the operator confirms", async ({ page }) => {
  await mockBackend(page);
  await advanceToSummary(page);

  // The primary button reads "Instalar" but is disabled before confirmation.
  const next = page.getByTestId("wizard-next");
  await expect(next).toContainText("Instalar");
  await expect(next).toBeDisabled();

  // Ticking the confirm gate enables it; clicking advances to the install step.
  await page.getByTestId("input-summary-confirm").check();
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.getByTestId("step-install")).toBeVisible();
});

test("going back from summary un-confirms the gate", async ({ page }) => {
  await mockBackend(page);
  await advanceToSummary(page);

  await page.getByTestId("input-summary-confirm").check();
  await expect(page.getByTestId("input-summary-confirm")).toBeChecked();

  // Back to tenant, then forward to summary again — confirmation is reset.
  await page.getByTestId("wizard-back").click();
  await expect(page.getByTestId("step-tenant")).toBeVisible();
  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("step-summary")).toBeVisible();

  await expect(page.getByTestId("input-summary-confirm")).not.toBeChecked();
  await expect(page.getByTestId("wizard-next")).toBeDisabled();
});

test("GPU note appears only when GPU acceleration is enabled", async ({ page }) => {
  await mockBackend(page);

  // First pass: GPU disabled (default) -> no GPU note.
  await advanceToSummary(page);
  await expect(page.getByTestId("estimate-gpu")).toHaveCount(0);

  // Enable GPU on the resources step, then return to summary.
  await page.getByTestId("stepper-item-resources").click();
  await expect(page.getByTestId("step-resources")).toBeVisible();
  await page.getByTestId("input-gpuEnabled").check();
  await page.getByTestId("stepper-item-summary").click();

  await expect(page.getByTestId("step-summary")).toBeVisible();
  await expect(page.getByTestId("estimate-gpu")).toBeVisible();
});
