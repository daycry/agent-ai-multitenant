"use client";

import { type FieldErrors, type OllamaMode, type ResourceConfig } from "@/lib/config";

import { PrereqPanel } from "../prereq-panel";
import { Field, NumberInput, Select, TextInput } from "./fields";

const OLLAMA_MODE_OPTIONS: ReadonlyArray<{ value: OllamaMode; label: string }> = [
  { value: "none", label: "Ninguno — usar Ollama externo/cloud o solo BM25" },
  { value: "cpu", label: "CPU — embeddings locales (recomendado)" },
  { value: "gpu", label: "GPU (CUDA) — además LLMs locales acelerados" },
];

// Embedders del catálogo curado compatibles (768 dims) — sugerencias para el
// campo (en lockstep con ingestion/embedding_models.recommended_models).
const RECOMMENDED_EMBEDDERS = [
  "nomic-embed-text",
  "snowflake-arctic-embed:110m",
  "granite-embedding:278m",
  "paraphrase-multilingual",
];

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

        <Field
          id="ollamaMode"
          label="Ollama en el stack"
          error={errors.ollamaMode}
          hint="CPU basta para embeddings locales; GPU (CUDA) requiere GPU NVIDIA + NVIDIA Container Toolkit (en Windows: Docker Desktop + WSL2). «Ninguno» usa un Ollama externo/cloud o se queda en búsqueda BM25."
        >
          <Select
            id="ollamaMode"
            value={value.ollamaMode}
            onChange={(ollamaMode) => onChange({ ollamaMode })}
            options={OLLAMA_MODE_OPTIONS}
          />
        </Field>

        {value.ollamaMode !== "none" && (
          <Field
            id="embeddingModel"
            label="Modelo de embeddings (se descargará al instalar)"
            error={errors.embeddingModel}
            hint={`Nombre real del registro Ollama, 768 dims. Recomendados: ${RECOMMENDED_EMBEDDERS.join(", ")}.`}
          >
            <TextInput
              id="embeddingModel"
              value={value.embeddingModel}
              onChange={(embeddingModel) => onChange({ embeddingModel })}
              placeholder="nomic-embed-text"
              error={Boolean(errors.embeddingModel)}
            />
          </Field>
        )}
      </div>
    </section>
  );
}
