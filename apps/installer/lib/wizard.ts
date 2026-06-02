/**
 * The 9-step installer wizard flow, mirrored from the FastAPI backend
 * (`apps/installer/backend/src/installer_backend/wizard.py`). Kept here as the
 * single client-side source of truth for step ordering and titles so the
 * wizard shell can render and navigate before the backend is reachable.
 *
 * Phase A (task_15_01) ships the SHELL: ordering, titles and a pure
 * forward/back state machine. The per-step capture forms (steps 2–6) are
 * filled by tasks 15_02–15_06.
 */

export type WizardStepId =
  | "welcome"
  | "basics"
  | "resources"
  | "storage"
  | "providers"
  | "tenant"
  | "summary"
  | "install"
  | "done";

export interface WizardStep {
  readonly id: WizardStepId;
  readonly titleEs: string;
  readonly titleEn: string;
  /** The step a human confirms before the irreversible install begins. */
  readonly isConfirmation?: boolean;
}

/** Canonical ordering — the array IS the source of truth for next/previous. */
export const WIZARD_STEPS: readonly WizardStep[] = [
  { id: "welcome", titleEs: "Bienvenida", titleEn: "Welcome" },
  { id: "basics", titleEs: "Configuración básica", titleEn: "Basic configuration" },
  { id: "resources", titleEs: "Recursos / GPU", titleEn: "Resources / GPU" },
  { id: "storage", titleEs: "Almacenamiento", titleEn: "Storage" },
  { id: "providers", titleEs: "Providers LLM", titleEn: "LLM providers" },
  { id: "tenant", titleEs: "Tenant inicial", titleEn: "Initial tenant" },
  { id: "summary", titleEs: "Resumen", titleEn: "Summary", isConfirmation: true },
  { id: "install", titleEs: "Instalación", titleEn: "Install" },
  { id: "done", titleEs: "Listo", titleEn: "Done" },
] as const;

export const CONFIRMATION_STEP: WizardStepId = "summary";

export function stepIndex(id: WizardStepId): number {
  const idx = WIZARD_STEPS.findIndex((s) => s.id === id);
  if (idx < 0) {
    throw new Error(`unknown wizard step: ${id}`);
  }
  return idx;
}

export function stepById(id: WizardStepId): WizardStep {
  return WIZARD_STEPS[stepIndex(id)];
}

export function nextStepId(id: WizardStepId): WizardStepId | null {
  const idx = stepIndex(id);
  return idx + 1 < WIZARD_STEPS.length ? WIZARD_STEPS[idx + 1].id : null;
}

export function previousStepId(id: WizardStepId): WizardStepId | null {
  const idx = stepIndex(id);
  return idx > 0 ? WIZARD_STEPS[idx - 1].id : null;
}

export function isFirstStep(id: WizardStepId): boolean {
  return stepIndex(id) === 0;
}

export function isLastStep(id: WizardStepId): boolean {
  return stepIndex(id) === WIZARD_STEPS.length - 1;
}
