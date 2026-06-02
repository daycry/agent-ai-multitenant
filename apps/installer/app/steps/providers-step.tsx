"use client";

import { type FieldErrors, type ProvidersConfig } from "@/lib/config";

import { Checkbox, Field, TextInput } from "./fields";

interface ProvidersStepProps {
  value: ProvidersConfig;
  errors: FieldErrors;
  onChange: (partial: Partial<ProvidersConfig>) => void;
}

/**
 * Step 5 — LLM providers (task_15_03). The four ADR-0021 paths form a CLOSED
 * catalogue: Claude Agent SDK, GitHub Copilot, Azure AI Foundry (via APIM) and
 * Ollama. At least one must be enabled. Each enabled provider requires its
 * credential/endpoint; all credentials are WRITE-ONLY (password inputs, held in
 * wizard state until POST, never echoed back by the backend).
 */
export function ProvidersStep({ value, errors, onChange }: ProvidersStepProps) {
  return (
    <section data-testid="step-providers" className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Providers LLM</h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          Habilita al menos uno de los cuatro proveedores soportados (ADR-0021). Las credenciales se
          guardan de forma segura y no se vuelven a mostrar.
        </p>
      </header>

      {errors.providers && (
        <p data-testid="providers-error" className="text-sm text-red-500">
          {errors.providers}
        </p>
      )}

      {/* Claude Agent SDK */}
      <div
        data-testid="provider-claude_sdk"
        className="border-border flex flex-col gap-3 rounded-md border p-4"
      >
        <Checkbox
          id="claudeSdk-enabled"
          checked={value.claudeSdk.enabled}
          onChange={(enabled) => onChange({ claudeSdk: { ...value.claudeSdk, enabled } })}
          label="Claude Agent SDK (suscripción Pro/Max)"
        />
        {value.claudeSdk.enabled && (
          <Field
            id="claudeSdk-oauthToken"
            label="Token OAuth"
            error={errors["claudeSdk.oauthToken"]}
          >
            <TextInput
              id="claudeSdk-oauthToken"
              value={value.claudeSdk.oauthToken}
              onChange={(oauthToken) => onChange({ claudeSdk: { ...value.claudeSdk, oauthToken } })}
              secret
              error={Boolean(errors["claudeSdk.oauthToken"])}
            />
          </Field>
        )}
      </div>

      {/* GitHub Copilot */}
      <div
        data-testid="provider-copilot"
        className="border-border flex flex-col gap-3 rounded-md border p-4"
      >
        <Checkbox
          id="copilot-enabled"
          checked={value.copilot.enabled}
          onChange={(enabled) => onChange({ copilot: { ...value.copilot, enabled } })}
          label="GitHub Copilot (OAuth Device Flow)"
        />
        {value.copilot.enabled && (
          <Field id="copilot-oauthToken" label="Token OAuth" error={errors["copilot.oauthToken"]}>
            <TextInput
              id="copilot-oauthToken"
              value={value.copilot.oauthToken}
              onChange={(oauthToken) => onChange({ copilot: { ...value.copilot, oauthToken } })}
              secret
              error={Boolean(errors["copilot.oauthToken"])}
            />
          </Field>
        )}
      </div>

      {/* Azure AI Foundry via APIM */}
      <div
        data-testid="provider-azure_foundry"
        className="border-border flex flex-col gap-3 rounded-md border p-4"
      >
        <Checkbox
          id="azureFoundry-enabled"
          checked={value.azureFoundry.enabled}
          onChange={(enabled) => onChange({ azureFoundry: { ...value.azureFoundry, enabled } })}
          label="Azure AI Foundry (gateway APIM)"
        />
        {value.azureFoundry.enabled && (
          <>
            <Field
              id="azureFoundry-apimEndpoint"
              label="Endpoint APIM"
              error={errors["azureFoundry.apimEndpoint"]}
            >
              <TextInput
                id="azureFoundry-apimEndpoint"
                value={value.azureFoundry.apimEndpoint}
                onChange={(apimEndpoint) =>
                  onChange({ azureFoundry: { ...value.azureFoundry, apimEndpoint } })
                }
                placeholder="https://apim.example.com/openai"
                error={Boolean(errors["azureFoundry.apimEndpoint"])}
              />
            </Field>
            <Field id="azureFoundry-apiKey" label="API key" error={errors["azureFoundry.apiKey"]}>
              <TextInput
                id="azureFoundry-apiKey"
                value={value.azureFoundry.apiKey}
                onChange={(apiKey) => onChange({ azureFoundry: { ...value.azureFoundry, apiKey } })}
                secret
                error={Boolean(errors["azureFoundry.apiKey"])}
              />
            </Field>
          </>
        )}
      </div>

      {/* Ollama */}
      <div
        data-testid="provider-ollama"
        className="border-border flex flex-col gap-3 rounded-md border p-4"
      >
        <Checkbox
          id="ollama-enabled"
          checked={value.ollama.enabled}
          onChange={(enabled) => onChange({ ollama: { ...value.ollama, enabled } })}
          label="Ollama (local o cloud)"
        />
        {value.ollama.enabled && (
          <Field id="ollama-endpoint" label="Endpoint" error={errors["ollama.endpoint"]}>
            <TextInput
              id="ollama-endpoint"
              value={value.ollama.endpoint}
              onChange={(endpoint) => onChange({ ollama: { ...value.ollama, endpoint } })}
              placeholder="http://localhost:11434"
              error={Boolean(errors["ollama.endpoint"])}
            />
          </Field>
        )}
      </div>
    </section>
  );
}
