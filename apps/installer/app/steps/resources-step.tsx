"use client";

import { type FieldErrors, type ResourceConfig } from "@/lib/config";

import { PrereqPanel } from "../prereq-panel";
import { Checkbox, Field, NumberInput } from "./fields";

interface ResourcesStepProps {
  value: ResourceConfig;
  errors: FieldErrors;
  onChange: (partial: Partial<ResourceConfig>) => void;
  /** Forwarded to the prereq panel so the shell can gate "next" on it. */
  onGateChange?: (canProceed: boolean) => void;
}

/**
 * Step 3 — resources / GPU (task_15_03). Combines the prerequisite probe panel
 * (task_15_02, which gates "next") with the resource-allocation capture: worker
 * replicas, per-worker memory, and the optional GPU enablement toggle. The
 * prereq panel keeps owning the install gate; the allocation form feeds the
 * generated compose (Phase B).
 */
export function ResourcesStep({ value, errors, onChange, onGateChange }: ResourcesStepProps) {
  return (
    <section data-testid="step-resources" className="flex flex-col gap-6">
      <PrereqPanel onGateChange={onGateChange} embedded />

      <div className="border-border flex flex-col gap-5 border-t pt-6">
        <header className="flex flex-col gap-1">
          <h3 className="text-lg font-semibold tracking-tight">Asignación de recursos</h3>
          <p className="text-muted-foreground max-w-prose text-sm">
            Ajusta cuántos workers se lanzan y la memoria por worker. Si la máquina tiene GPU
            NVIDIA, puedes habilitar la aceleración por GPU.
          </p>
        </header>

        <div className="grid gap-5 sm:grid-cols-2">
          <Field id="workerReplicas" label="Réplicas de worker" error={errors.workerReplicas}>
            <NumberInput
              id="workerReplicas"
              value={value.workerReplicas}
              onChange={(workerReplicas) => onChange({ workerReplicas })}
              min={1}
              max={64}
              error={Boolean(errors.workerReplicas)}
            />
          </Field>

          <Field
            id="workerMemoryGib"
            label="Memoria por worker (GiB)"
            error={errors.workerMemoryGib}
          >
            <NumberInput
              id="workerMemoryGib"
              value={value.workerMemoryGib}
              onChange={(workerMemoryGib) => onChange({ workerMemoryGib })}
              min={1}
              max={512}
              error={Boolean(errors.workerMemoryGib)}
            />
          </Field>
        </div>

        <Checkbox
          id="gpuEnabled"
          checked={value.gpuEnabled}
          onChange={(gpuEnabled) => onChange({ gpuEnabled })}
          label="Habilitar aceleración por GPU (requiere GPU NVIDIA detectada)"
        />
      </div>
    </section>
  );
}
