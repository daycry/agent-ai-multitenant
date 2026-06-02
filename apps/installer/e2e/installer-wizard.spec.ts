import { expect, test } from "@playwright/test";

/**
 * E2E for the installer wizard SHELL (Plan 15 task_15_01).
 *
 * Asserts the Phase-A deliverable: the 9-step wizard renders, the stepper
 * lists every step, and forward/back navigation walks the flow against the
 * client-side state machine. No backend and no real provisioning is involved
 * — the install/finalize behaviour is filled by tasks 15_05/15_06 and the
 * real install is a HUMAN test in the plan.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification on a host with the
 * installer dev server up). The auto check `auto_15_01_a` runs:
 *   npx playwright test e2e/installer-wizard.spec.ts
 */

test("wizard shell renders welcome step and the 9-step stepper", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("wizard-title")).toBeVisible();
  await expect(page.getByTestId("wizard-stepper")).toBeVisible();
  await expect(page.getByTestId("step-welcome")).toBeVisible();

  // All nine steps appear in the stepper, in order.
  const stepIds = [
    "welcome",
    "basics",
    "resources",
    "storage",
    "providers",
    "tenant",
    "summary",
    "install",
    "done",
  ];
  for (const id of stepIds) {
    await expect(page.getByTestId(`stepper-item-${id}`)).toBeVisible();
  }

  // Progress reads "Paso 1 de 9".
  await expect(page.getByTestId("wizard-progress")).toContainText("Paso 1 de 9");
});

test("back is disabled on the first step", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("wizard-back")).toBeDisabled();
});

test("next advances from welcome to basics", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await page.getByTestId("wizard-next").click();

  await expect(page.getByTestId("step-basics")).toBeVisible();
  await expect(page.getByTestId("wizard-progress")).toContainText("Paso 2 de 9");
  // Back is now enabled.
  await expect(page.getByTestId("wizard-back")).toBeEnabled();
});

test("back returns to the previous step", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await page.getByTestId("wizard-next").click();
  await expect(page.getByTestId("step-basics")).toBeVisible();

  await page.getByTestId("wizard-back").click();
  await expect(page.getByTestId("step-welcome")).toBeVisible();
  await expect(page.getByTestId("wizard-progress")).toContainText("Paso 1 de 9");
});

test("a not-yet-visited step in the stepper is disabled", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  // Standing on welcome, the summary step has not been reached yet.
  await expect(page.getByTestId("stepper-item-summary")).toBeDisabled();
});

test("navigating to the summary step swaps the primary button to Instalar", async ({ page }) => {
  // The config steps (2-6, task_15_03) now gate "next" on client-side
  // validation and the resources step gates on the prereq probe, so mock the
  // backend and fill each form before advancing. The detailed per-field
  // validation lives in installer-steps.spec.ts; here we only walk to summary.
  await page.route("**/api/prereqs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        results: [
          {
            key: "docker",
            label: "Docker",
            status: "ok",
            ok: true,
            detail: "",
            remediation: "",
            required: true,
          },
        ],
        all_required_ok: true,
        can_proceed: true,
      }),
    });
  });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await page.getByTestId("wizard-next").click(); // welcome -> basics
  await page.getByTestId("input-domain").fill("agentic.example.com");
  await page.getByTestId("wizard-next").click(); // basics -> resources
  await expect(page.getByTestId("step-resources")).toBeVisible();
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
  await expect(page.getByTestId("wizard-next")).toContainText("Instalar");
});
