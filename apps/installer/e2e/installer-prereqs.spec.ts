import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for step 1 — prerequisite validation (Plan 15 task_15_02).
 *
 * Asserts the wizard surfaces the backend's tri-state prereq results and gates
 * the "next" button: a hard failure on a required check blocks proceeding; an
 * optional GPU warning does not. The backend's `/api/prereqs` call is MOCKED
 * via route interception so no real host probing is needed.
 *
 * WRITTEN-NOT-RUN in Phase A (pending human verification with the installer
 * dev server up). The auto check `auto_15_02_a` runs the pytest logic test;
 * this spec backs the human run of the wizard UI.
 */

interface PrereqItem {
  key: string;
  label: string;
  status: "ok" | "warn" | "fail";
  ok: boolean;
  detail: string;
  remediation: string;
  required: boolean;
}

interface PrereqResponse {
  results: PrereqItem[];
  all_required_ok: boolean;
  can_proceed: boolean;
}

const HEALTHY: PrereqResponse = {
  results: [
    {
      key: "docker",
      label: "Docker Engine",
      status: "ok",
      ok: true,
      detail: "Docker 27.1 detectado.",
      remediation: "",
      required: true,
    },
    {
      key: "compose",
      label: "Docker Compose v2",
      status: "ok",
      ok: true,
      detail: "Compose 2.29 detectado.",
      remediation: "",
      required: true,
    },
    {
      key: "ram",
      label: "RAM >= 8 GiB",
      status: "ok",
      ok: true,
      detail: "16.0 GiB de RAM disponibles.",
      remediation: "",
      required: true,
    },
    {
      key: "disk",
      label: "Disco libre >= 50 GiB",
      status: "ok",
      ok: true,
      detail: "200.0 GiB libres.",
      remediation: "",
      required: true,
    },
    {
      key: "gpu",
      label: "GPU NVIDIA (opcional)",
      status: "ok",
      ok: true,
      detail: "NVIDIA L4",
      remediation: "",
      required: false,
    },
  ],
  all_required_ok: true,
  can_proceed: true,
};

const DOCKER_MISSING: PrereqResponse = {
  results: [
    {
      key: "docker",
      label: "Docker Engine",
      status: "fail",
      ok: false,
      detail: "Docker no detectado.",
      remediation:
        "Instala Docker Engine (>= 24.0) y asegúrate de que el demonio está en ejecución.",
      required: true,
    },
    {
      key: "compose",
      label: "Docker Compose v2",
      status: "fail",
      ok: false,
      detail: "Docker Compose v2 no detectado.",
      remediation: "Instala el plugin Docker Compose v2.",
      required: true,
    },
    {
      key: "ram",
      label: "RAM >= 8 GiB",
      status: "ok",
      ok: true,
      detail: "16.0 GiB de RAM disponibles.",
      remediation: "",
      required: true,
    },
    {
      key: "disk",
      label: "Disco libre >= 50 GiB",
      status: "ok",
      ok: true,
      detail: "200.0 GiB libres.",
      remediation: "",
      required: true,
    },
    {
      key: "gpu",
      label: "GPU NVIDIA (opcional)",
      status: "warn",
      ok: true,
      detail: "No se detectó ninguna GPU NVIDIA.",
      remediation: "La GPU es opcional: el stack funciona en CPU.",
      required: false,
    },
  ],
  all_required_ok: false,
  can_proceed: false,
};

const GPU_ABSENT: PrereqResponse = {
  results: [
    {
      key: "docker",
      label: "Docker Engine",
      status: "ok",
      ok: true,
      detail: "Docker 27.1 detectado.",
      remediation: "",
      required: true,
    },
    {
      key: "compose",
      label: "Docker Compose v2",
      status: "ok",
      ok: true,
      detail: "Compose 2.29 detectado.",
      remediation: "",
      required: true,
    },
    {
      key: "ram",
      label: "RAM >= 8 GiB",
      status: "ok",
      ok: true,
      detail: "16.0 GiB de RAM disponibles.",
      remediation: "",
      required: true,
    },
    {
      key: "disk",
      label: "Disco libre >= 50 GiB",
      status: "ok",
      ok: true,
      detail: "200.0 GiB libres.",
      remediation: "",
      required: true,
    },
    {
      key: "gpu",
      label: "GPU NVIDIA (opcional)",
      status: "warn",
      ok: true,
      detail: "No se detectó ninguna GPU NVIDIA.",
      remediation: "La GPU es opcional: el stack funciona en CPU.",
      required: false,
    },
  ],
  all_required_ok: true,
  can_proceed: true,
};

/** Mock the backend prereq endpoint regardless of host/port. */
async function mockPrereqs(page: Page, body: PrereqResponse): Promise<void> {
  await page.route("**/api/prereqs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

/** Advance from welcome to the resources (prereq) step. */
async function gotoResourcesStep(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByTestId("wizard-next").click(); // welcome -> basics
  await page.getByTestId("wizard-next").click(); // basics  -> resources
  await expect(page.getByTestId("step-resources")).toBeVisible();
}

test("all prerequisites pass — checks render and next is enabled", async ({ page }) => {
  await mockPrereqs(page, HEALTHY);
  await gotoResourcesStep(page);

  for (const key of ["docker", "compose", "ram", "disk", "gpu"]) {
    await expect(page.getByTestId(`prereq-item-${key}`)).toBeVisible();
    await expect(page.getByTestId(`prereq-item-${key}`)).toHaveAttribute("data-status", "ok");
  }
  await expect(page.getByTestId("prereq-blocked")).toHaveCount(0);
  await expect(page.getByTestId("wizard-next")).toBeEnabled();
});

test("missing Docker — fails with remediation and blocks next", async ({ page }) => {
  await mockPrereqs(page, DOCKER_MISSING);
  await gotoResourcesStep(page);

  await expect(page.getByTestId("prereq-item-docker")).toHaveAttribute("data-status", "fail");
  await expect(page.getByTestId("prereq-remediation-docker")).toContainText("Instala Docker");
  await expect(page.getByTestId("prereq-blocked")).toBeVisible();
  await expect(page.getByTestId("wizard-next")).toBeDisabled();
});

test("GPU absent — warns but does not block next", async ({ page }) => {
  await mockPrereqs(page, GPU_ABSENT);
  await gotoResourcesStep(page);

  await expect(page.getByTestId("prereq-item-gpu")).toHaveAttribute("data-status", "warn");
  await expect(page.getByTestId("prereq-blocked")).toHaveCount(0);
  await expect(page.getByTestId("wizard-next")).toBeEnabled();
});
