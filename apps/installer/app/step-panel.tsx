"use client";

import { stepById, type WizardStepId } from "@/lib/wizard";

interface StepPanelProps {
  step: WizardStepId;
}

/**
 * Renders the body of the current wizard step. Phase A (task_15_01) ships the
 * Welcome step in full and a typed placeholder for every other step; tasks
 * 15_02–15_06 replace each placeholder with its real form / panel:
 *
 *   resources/storage/providers/tenant/basics → 15_03 (capture forms)
 *   summary  → 15_04 (resource preview + confirm)
 *   install  → 15_05 (progress + live logs)
 *   done     → 15_06 (one-shot credentials + self-destruct)
 */
export function StepPanel({ step }: StepPanelProps) {
  const meta = stepById(step);

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
