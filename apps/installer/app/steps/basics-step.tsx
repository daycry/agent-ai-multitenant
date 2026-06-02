"use client";

import { type Environment, type FieldErrors, type SystemConfig } from "@/lib/config";

import { Field, Select, TextInput } from "./fields";

interface BasicsStepProps {
  value: SystemConfig;
  errors: FieldErrors;
  onChange: (partial: Partial<SystemConfig>) => void;
}

const ENVIRONMENTS: ReadonlyArray<{ value: Environment; label: string }> = [
  { value: "development", label: "Desarrollo" },
  { value: "staging", label: "Staging" },
  { value: "production", label: "Producción" },
];

/**
 * Step 2 — system basics (task_15_03): the domain the platform is served on and
 * the deployment environment profile. Client-side validated; captured into
 * wizard state and re-validated server-side.
 */
export function BasicsStep({ value, errors, onChange }: BasicsStepProps) {
  return (
    <section data-testid="step-basics" className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Configuración básica</h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          Define el dominio en el que se servirá la plataforma y el perfil de entorno.
        </p>
      </header>

      <Field
        id="domain"
        label="Dominio"
        error={errors.domain}
        hint="Nombre de host o FQDN sin esquema ni ruta (p. ej. agentic.example.com)."
      >
        <TextInput
          id="domain"
          value={value.domain}
          onChange={(domain) => onChange({ domain })}
          placeholder="agentic.example.com"
          error={Boolean(errors.domain)}
        />
      </Field>

      <Field id="environment" label="Entorno">
        <Select<Environment>
          id="environment"
          value={value.environment}
          onChange={(environment) => onChange({ environment })}
          options={ENVIRONMENTS}
        />
      </Field>
    </section>
  );
}
