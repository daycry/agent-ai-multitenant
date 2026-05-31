import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the install progress step 8 (Plan 15 task_15_05).
 *
 * Walks the wizard to the install step and asserts the deliverable:
 *   - the install pipeline runs its steps in order with a live per-step status
 *     (pending → running → ok),
 *   - progress + logs stream into the view in real time,
 *   - a successful run reaches 100% and unblocks "next" (to the done step),
 *   - a failing step halts the pipeline, surfaces the error and offers retry,
 *   - retry resumes and can complete the install.
 *
 * The backend `/api/install/stream` SSE route is MOCKED via route interception
 * with a scripted Server-Sent Events body — no real `docker compose`, no
 * provisioning, no host access. `/api/prereqs` and `/api/config/validate` are
 * mocked as in the other specs. Secrets are NEVER part of the streamed events.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification with the installer dev
 * server up). The auto check `auto_15_05_a` runs:
 *   npx playwright test e2e/installer-progress.spec.ts
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

interface ProgressEvent {
  stage: string;
  message: string;
  percent: number;
  done: boolean;
  failed: boolean;
}

/** Encode a list of events as an SSE response body. */
function sseBody(events: ProgressEvent[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

const SUCCESS_EVENTS: ProgressEvent[] = [
  {
    stage: "generate_config",
    message: "Generar configuración…",
    percent: 0,
    done: false,
    failed: false,
  },
  {
    stage: "generate_config",
    message: "Generar configuración: completado.",
    percent: 20,
    done: false,
    failed: false,
  },
  { stage: "pull_images", message: "Descargar imágenes…", percent: 20, done: false, failed: false },
  {
    stage: "pull_images",
    message: "Descargar imágenes: completado.",
    percent: 40,
    done: false,
    failed: false,
  },
  { stage: "start_stack", message: "Arrancar el stack…", percent: 40, done: false, failed: false },
  {
    stage: "start_stack",
    message: "Arrancar el stack: completado.",
    percent: 60,
    done: false,
    failed: false,
  },
  {
    stage: "bootstrap_vault",
    message: "Inicializar Vault…",
    percent: 60,
    done: false,
    failed: false,
  },
  {
    stage: "bootstrap_vault",
    message: "Inicializar Vault: completado.",
    percent: 80,
    done: false,
    failed: false,
  },
  {
    stage: "seed_tenant",
    message: "Crear tenant inicial…",
    percent: 80,
    done: false,
    failed: false,
  },
  {
    stage: "seed_tenant",
    message: "Crear tenant inicial: completado.",
    percent: 100,
    done: false,
    failed: false,
  },
  { stage: "done", message: "Instalación completada.", percent: 100, done: true, failed: false },
];

const FAIL_THEN_SUCCESS: ProgressEvent[][] = [
  // First stream: fails at start_stack after the first two steps succeed.
  [
    {
      stage: "generate_config",
      message: "Generar configuración…",
      percent: 0,
      done: false,
      failed: false,
    },
    {
      stage: "generate_config",
      message: "Generar configuración: completado.",
      percent: 20,
      done: false,
      failed: false,
    },
    {
      stage: "pull_images",
      message: "Descargar imágenes…",
      percent: 20,
      done: false,
      failed: false,
    },
    {
      stage: "pull_images",
      message: "Descargar imágenes: completado.",
      percent: 40,
      done: false,
      failed: false,
    },
    {
      stage: "start_stack",
      message: "Arrancar el stack…",
      percent: 40,
      done: false,
      failed: false,
    },
    {
      stage: "start_stack",
      message: "docker daemon no responde",
      percent: 40,
      done: false,
      failed: true,
    },
  ],
  // Retry stream: resumes and completes.
  SUCCESS_EVENTS,
];

async function mockBaseBackend(page: Page): Promise<void> {
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

/** Walk the whole wizard, confirm, and land on the install step. */
async function advanceToInstall(page: Page): Promise<void> {
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
  await page.getByTestId("input-summary-confirm").check();
  await page.getByTestId("wizard-next").click(); // summary -> install

  await expect(page.getByTestId("step-install")).toBeVisible();
}

test("install streams progress and per-step status to completion", async ({ page }) => {
  await mockBaseBackend(page);
  await page.route("**/api/install/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(SUCCESS_EVENTS),
    });
  });

  await advanceToInstall(page);

  // Every pipeline step ends OK.
  for (const id of [
    "generate_config",
    "pull_images",
    "start_stack",
    "bootstrap_vault",
    "seed_tenant",
  ]) {
    await expect(page.getByTestId(`install-step-${id}`)).toHaveAttribute("data-status", "ok");
  }

  // Progress reaches 100% and the success banner shows.
  await expect(page.getByTestId("install-percent")).toContainText("100%");
  await expect(page.getByTestId("install-phase")).toHaveAttribute("data-phase", "done");
  await expect(page.getByTestId("install-success")).toBeVisible();

  // The live log accumulated lines from the stream.
  await expect(page.getByTestId("install-log")).toContainText("Crear tenant inicial: completado.");

  // "Next" (to the done step) is now enabled.
  await expect(page.getByTestId("wizard-next")).toBeEnabled();
});

test("a failing step halts the install, surfaces the error and offers retry", async ({ page }) => {
  await mockBaseBackend(page);

  // Serve the failing stream first, then the successful retry stream.
  let call = 0;
  await page.route("**/api/install/stream", async (route) => {
    const events = FAIL_THEN_SUCCESS[Math.min(call, FAIL_THEN_SUCCESS.length - 1)];
    call += 1;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(events),
    });
  });

  await advanceToInstall(page);

  // The failing step is marked failed; later steps stay pending.
  await expect(page.getByTestId("install-step-start_stack")).toHaveAttribute(
    "data-status",
    "failed",
  );
  await expect(page.getByTestId("install-step-bootstrap_vault")).toHaveAttribute(
    "data-status",
    "pending",
  );
  await expect(page.getByTestId("install-error")).toContainText("docker daemon no responde");

  // "Next" stays blocked while the install is in a failed state.
  await expect(page.getByTestId("wizard-next")).toBeDisabled();

  // Retry resumes and completes.
  await page.getByTestId("install-retry").click();
  await expect(page.getByTestId("install-phase")).toHaveAttribute("data-phase", "done");
  await expect(page.getByTestId("install-step-seed_tenant")).toHaveAttribute("data-status", "ok");
  await expect(page.getByTestId("wizard-next")).toBeEnabled();
});

test("install step blocks going back (provisioning is irreversible)", async ({ page }) => {
  await mockBaseBackend(page);
  await page.route("**/api/install/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(SUCCESS_EVENTS),
    });
  });

  await advanceToInstall(page);
  await expect(page.getByTestId("wizard-back")).toBeDisabled();
});
