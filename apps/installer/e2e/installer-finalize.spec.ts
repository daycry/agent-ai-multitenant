import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the finalize step 9 (Plan 15 task_15_06).
 *
 * Walks the wizard through a successful install to the done step and asserts the
 * deliverable:
 *   - the generated credentials + Vault unseal keys are shown EXACTLY ONCE
 *     (Decisiones Clave: no recovery — the operator must save them),
 *   - a prominent "save these now, shown only once" warning is rendered,
 *   - once the backend has served the one-time reveal a re-mount gets `410 Gone`
 *     and the UI shows the "already shown" state instead of any secret,
 *   - an incomplete install reveals nothing (`409` → "no credentials").
 *
 * The backend routes are MOCKED via route interception — no real `docker
 * compose`, no provisioning, no self-destruct. `/api/finalize/reveal` is served
 * once with the payload and thereafter as `410 Gone`, mirroring the backend's
 * one-time reveal. Secrets are present ONLY in the single mocked reveal body.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification with the installer dev
 * server up). The auto check `auto_15_06_a` runs the pytest backend test; this
 * spec is exercised with the dev server up:
 *   npx playwright test e2e/installer-finalize.spec.ts
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

// The one-time reveal payload. These secret values must appear ONLY in the
// single mocked reveal body — never anywhere else in the page.
const ADMIN_PASSWORD = "Adm1n-Pass-shown-once-xyz";
const ROOT_TOKEN = "vault-root-token-shown-once-xyz";
const UNSEAL_KEYS = ["unseal-share-one-xyz", "unseal-share-two-xyz", "unseal-share-three-xyz"];

const REVEAL_BODY = {
  credentials: [
    {
      key: "admin_username",
      label_es: "Usuario administrador",
      label_en: "Admin username",
      secret: "admin@acme.com",
    },
    {
      key: "admin_password",
      label_es: "Contraseña del administrador",
      label_en: "Admin password",
      secret: ADMIN_PASSWORD,
    },
    {
      key: "vault_root_token",
      label_es: "Token root de Vault",
      label_en: "Vault root token",
      secret: ROOT_TOKEN,
    },
  ],
  unseal_keys: UNSEAL_KEYS,
  warning_es:
    "Guarda estas credenciales y las unseal keys de Vault AHORA. Se muestran una sola vez y no hay forma de recuperarlas.",
  warning_en:
    "Save these credentials and Vault unseal keys NOW. They are shown only once and cannot be recovered.",
};

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
  await page.route("**/api/install/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(SUCCESS_EVENTS),
    });
  });
}

/** Walk the whole wizard through a successful install onto the done step. */
async function advanceToDone(page: Page): Promise<void> {
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
  await expect(page.getByTestId("install-phase")).toHaveAttribute("data-phase", "done");
  await page.getByTestId("wizard-next").click(); // install -> done

  await expect(page.getByTestId("step-done")).toBeVisible();
}

test("credentials + unseal keys are revealed exactly once with a save-now warning", async ({
  page,
}) => {
  await mockBaseBackend(page);

  // Serve the reveal once; any later call is 410 Gone (no secret in the body).
  let revealCalls = 0;
  await page.route("**/api/finalize/reveal", async (route) => {
    revealCalls += 1;
    if (revealCalls === 1) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(REVEAL_BODY),
      });
    } else {
      await route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Las credenciales ya se mostraron una vez." }),
      });
    }
  });

  await advanceToDone(page);

  // The one-time reveal is shown: every credential + unseal key + the warning.
  await expect(page.getByTestId("reveal-credentials")).toBeVisible();
  await expect(page.getByTestId("reveal-warning")).toContainText("una sola vez");
  await expect(page.getByTestId("credential-value-admin_password")).toContainText(ADMIN_PASSWORD);
  await expect(page.getByTestId("credential-value-vault_root_token")).toContainText(ROOT_TOKEN);
  await expect(page.getByTestId("unseal-keys").getByTestId("unseal-key")).toHaveCount(
    UNSEAL_KEYS.length,
  );
  await expect(page.getByTestId("unseal-keys")).toContainText(UNSEAL_KEYS[0]);

  // The reveal was requested exactly once.
  expect(revealCalls).toBe(1);
});

test("a re-visit after the one-time reveal shows the 'already shown' state, no secret", async ({
  page,
}) => {
  await mockBaseBackend(page);

  // The reveal is already gone (the operator already saw it once).
  await page.route("**/api/finalize/reveal", async (route) => {
    await route.fulfill({
      status: 410,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Las credenciales ya se mostraron una vez." }),
    });
  });

  await advanceToDone(page);

  await expect(page.getByTestId("reveal-gone")).toBeVisible();
  // No secret value is anywhere on the page.
  await expect(page.getByTestId("reveal-credentials")).toHaveCount(0);
  for (const secret of [ADMIN_PASSWORD, ROOT_TOKEN, ...UNSEAL_KEYS]) {
    await expect(page.locator("body")).not.toContainText(secret);
  }
});

test("an incomplete install reveals nothing", async ({ page }) => {
  await mockBaseBackend(page);

  // 409: the install never completed, so there is nothing to reveal.
  await page.route("**/api/finalize/reveal", async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "La instalación no se ha completado." }),
    });
  });

  await advanceToDone(page);

  await expect(page.getByTestId("reveal-incomplete")).toBeVisible();
  await expect(page.getByTestId("reveal-credentials")).toHaveCount(0);
});
