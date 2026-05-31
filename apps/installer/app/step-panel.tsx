"use client";

import {
  hasErrors,
  isConfigStep,
  validateStep,
  type FieldErrors,
  type InstallerConfig,
  type ProvidersConfig,
  type ResourceConfig,
  type StorageConfig,
  type SystemConfig,
  type TenantConfig,
} from "@/lib/config";
import { type ConfigController } from "@/lib/use-config";
import { stepById, type WizardStepId } from "@/lib/wizard";

import { BasicsStep } from "./steps/basics-step";
import { ProvidersStep } from "./steps/providers-step";
import { ResourcesStep } from "./steps/resources-step";
import { StorageStep } from "./steps/storage-step";
import { TenantStep } from "./steps/tenant-step";

interface StepPanelProps {
  step: WizardStepId;
  config: ConfigController;
  /** Show inline errors only after the user tried to advance. */
  showErrors: boolean;
  /** Forwarded to the prereq step so the shell can gate "next" on it. */
  onGateChange?: (canProceed: boolean) => void;
}

/**
 * Renders the body of the current wizard step. task_15_03 fills the capture
 * forms for steps 2-6 (basics / resources / storage / providers / tenant); the
 * summary (15_04), install progress (15_05) and finalize (15_06) steps keep a
 * typed placeholder until their tasks land.
 */
export function StepPanel({ step, config, showErrors, onGateChange }: StepPanelProps) {
  const meta = stepById(step);
  const errors: FieldErrors =
    showErrors && isConfigStep(step) ? validateStep(step, config.config) : {};

  if (step === "welcome") {
    return (
      <section data-testid="step-welcome" className="flex flex-col gap-4">
        <h2 className="text-2xl font-semibold tracking-tight">
          Bienvenido al instalador de <span className="text-brand-gradient">agentic-platform</span>
        </h2>
        <p className="text-muted-foreground max-w-prose">
          Este asistente te guiará en 9 pasos para configurar e instalar la plataforma en esta
          máquina. Comprobaremos los prerequisitos, capturaremos la configuración y aprovisionaremos
          el stack con Docker Compose.
        </p>
        <p className="text-muted-foreground max-w-prose text-sm">
          El instalador es temporal: una vez completada la instalación, este contenedor se
          autodestruye. Guarda las credenciales que se muestren al final, ya que solo se enseñan una
          vez.
        </p>
      </section>
    );
  }

  if (step === "basics") {
    return (
      <BasicsStep
        value={config.config.system}
        errors={errors}
        onChange={(partial: Partial<SystemConfig>) => config.patch("system", partial)}
      />
    );
  }

  if (step === "resources") {
    return (
      <ResourcesStep
        value={config.config.resources}
        errors={errors}
        onChange={(partial: Partial<ResourceConfig>) => config.patch("resources", partial)}
        onGateChange={onGateChange}
      />
    );
  }

  if (step === "storage") {
    return (
      <StorageStep
        value={config.config.storage}
        errors={errors}
        onChange={(partial: Partial<StorageConfig>) => config.patch("storage", partial)}
      />
    );
  }

  if (step === "providers") {
    return (
      <ProvidersStep
        value={config.config.providers}
        errors={errors}
        onChange={(partial: Partial<ProvidersConfig>) => config.patch("providers", partial)}
      />
    );
  }

  if (step === "tenant") {
    return (
      <TenantStep
        value={config.config.tenant}
        errors={errors}
        onChange={(partial: Partial<TenantConfig>) => config.patch("tenant", partial)}
      />
    );
  }

  return (
    <section data-testid={`step-${step}`} className="flex flex-col gap-3">
      <h2 className="text-2xl font-semibold tracking-tight">{meta.titleEs}</h2>
      <p className="text-muted-foreground max-w-prose">
        Este paso se completará en una tarea posterior del plan.
      </p>
      <p
        data-testid={`step-${step}-placeholder`}
        className="text-muted-foreground rounded-md border border-dashed border-border px-4 py-6 text-sm"
      >
        Contenido pendiente — {meta.titleEs} ({meta.titleEn}).
      </p>
    </section>
  );
}

/** Exported helper used by the shell to know if a config step blocks "next". */
export function stepHasBlockingErrors(step: WizardStepId, config: InstallerConfig): boolean {
  if (isConfigStep(step)) {
    return hasErrors(validateStep(step, config));
  }
  return false;
}
