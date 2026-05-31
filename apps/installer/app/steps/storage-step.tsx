"use client";

import { type FieldErrors, type StorageConfig } from "@/lib/config";

import { Field, TextInput } from "./fields";

interface StorageStepProps {
  value: StorageConfig;
  errors: FieldErrors;
  onChange: (partial: Partial<StorageConfig>) => void;
}

/**
 * Step 4 — storage (task_15_03): where persistent data lives + the MinIO object
 * store. The MinIO secret key is WRITE-ONLY: typed once, held in wizard state
 * until POST, never displayed back. Rendered as a password input.
 */
export function StorageStep({ value, errors, onChange }: StorageStepProps) {
  return (
    <section data-testid="step-storage" className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Almacenamiento</h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          Ruta de datos persistentes y configuración del almacén de objetos MinIO.
        </p>
      </header>

      <Field
        id="dataRoot"
        label="Ruta de datos"
        error={errors.dataRoot}
        hint="Ruta absoluta donde se guardan repos, base de datos y objetos."
      >
        <TextInput
          id="dataRoot"
          value={value.dataRoot}
          onChange={(dataRoot) => onChange({ dataRoot })}
          placeholder="/data/agent-platform"
          error={Boolean(errors.dataRoot)}
        />
      </Field>

      <Field
        id="minioBucket"
        label="Bucket de MinIO"
        error={errors.minioBucket}
        hint="3-63 caracteres en minúsculas, dígitos y guiones."
      >
        <TextInput
          id="minioBucket"
          value={value.minioBucket}
          onChange={(minioBucket) => onChange({ minioBucket })}
          placeholder="agentic-platform"
          error={Boolean(errors.minioBucket)}
        />
      </Field>

      <Field id="minioAccessKey" label="Access key de MinIO" error={errors.minioAccessKey}>
        <TextInput
          id="minioAccessKey"
          value={value.minioAccessKey}
          onChange={(minioAccessKey) => onChange({ minioAccessKey })}
          placeholder="minioadmin"
          error={Boolean(errors.minioAccessKey)}
        />
      </Field>

      <Field
        id="minioSecretKey"
        label="Secret key de MinIO"
        error={errors.minioSecretKey}
        hint="Se guarda de forma segura y no se vuelve a mostrar."
      >
        <TextInput
          id="minioSecretKey"
          value={value.minioSecretKey}
          onChange={(minioSecretKey) => onChange({ minioSecretKey })}
          secret
          error={Boolean(errors.minioSecretKey)}
        />
      </Field>
    </section>
  );
}
