"use client";

/**
 * Diálogo de alta / edición de un proveedor LLM.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid`, mismas reglas de validación y mismo cuerpo de la petición.
 *
 * **Disciplina de secreto** (ADR 0028, innegociable): los inputs de credencial
 * son WRITE-ONLY. Al editar nunca se recibe el valor —la API no lo devuelve
 * jamás— y el campo sólo muestra "configurado"; dejarlo vacío conserva el
 * secreto que ya está en Vault. Ninguna respuesta de esta pantalla contiene el
 * valor: sólo el booleano `has_credential`.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import {
  isKind,
  KIND_LABEL,
  KINDS,
  type LlmProvider,
  type ProviderKind,
} from "./llm-provider-types";

interface ProviderFormDialogProps {
  mode: "create" | "edit";
  provider?: LlmProvider;
  onClose: () => void;
  onSaved: () => void;
}

export function ProviderFormDialog({ mode, provider, onClose, onSaved }: ProviderFormDialogProps) {
  const t = useT("llmProviders");
  const errorText = useErrorText();
  const isEdit = mode === "edit";

  // `kind` is immutable on edit (a kind change is a different provider).
  const [kind, setKind] = useState<ProviderKind>(
    provider && isKind(provider.kind) ? provider.kind : "claude_sdk",
  );
  const [slug, setSlug] = useState(provider?.slug ?? "");
  const [displayName, setDisplayName] = useState(provider?.display_name ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [isActive, setIsActive] = useState(provider?.is_active ?? true);

  // Write-only credential inputs (one is meaningful per kind).
  const [oauthToken, setOauthToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  // claude_sdk has TWO auth modes on the same kind (ADR 0063): an Anthropic
  // API key (→ ANTHROPIC_API_KEY) or a Pro/Max subscription token from
  // `claude setup-token` (→ CLAUDE_CODE_OAUTH_TOKEN). The single credential
  // input is routed to the right field by this mode.
  const [claudeAuthMode, setClaudeAuthMode] = useState<"api_key" | "subscription">("api_key");

  const needsBaseUrl = kind === "azure_foundry" || kind === "ollama";

  // Append the meaningful credential field for the kind, ONLY when filled.
  function addCredential(body: Record<string, unknown>): void {
    if (kind === "claude_sdk") {
      // Route the single credential input to the field the backend maps to the
      // right env var (ADR 0063): API key → api_key (ANTHROPIC_API_KEY),
      // subscription → oauth_token (CLAUDE_CODE_OAUTH_TOKEN).
      if (oauthToken.trim() !== "") {
        if (claudeAuthMode === "api_key") body.api_key = oauthToken;
        else body.oauth_token = oauthToken;
      }
    } else if (kind === "copilot") {
      if (oauthToken.trim() !== "") body.oauth_token = oauthToken;
    } else if (kind === "azure_foundry") {
      if (apiKey.trim() !== "") body.api_key = apiKey;
    } else if (kind === "ollama") {
      if (bearerToken.trim() !== "") body.bearer_token = bearerToken;
    }
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const trimmedBase = baseUrl.trim();
      if (isEdit && provider) {
        // PUT: send only what changed; `kind` is immutable. A blank secret
        // input means "keep the current Vault secret" (omit it).
        const body: Record<string, unknown> = {
          slug: slug.trim(),
          display_name: displayName.trim(),
          base_url: trimmedBase === "" ? null : trimmedBase,
          is_active: isActive,
        };
        addCredential(body);
        return apiFetch<LlmProvider>(`/admin/llm-providers/${provider.id}`, {
          method: "PUT",
          body,
        });
      }
      const body: Record<string, unknown> = {
        kind,
        slug: slug.trim(),
        display_name: displayName.trim(),
        base_url: trimmedBase === "" ? null : trimmedBase,
        is_active: isActive,
      };
      addCredential(body);
      return apiFetch<LlmProvider>("/admin/llm-providers", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  // Per-kind required-fields gate (mirrors the backend validator).
  const credentialFilled =
    (kind === "claude_sdk" || kind === "copilot") && oauthToken.trim() !== "";
  const apiKeyFilled = kind === "azure_foundry" && apiKey.trim() !== "";
  // Mirror the backend slug rule (kebab-case) for instant feedback.
  const slugValid = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(slug.trim());
  const canSave =
    slugValid &&
    displayName.trim() !== "" &&
    // base_url required for azure_foundry + ollama.
    (!needsBaseUrl || baseUrl.trim() !== "") &&
    // On create the required credential must be present; on edit it may be
    // kept (already in Vault), so we don't force it.
    (isEdit || kind === "ollama" || (kind === "azure_foundry" ? apiKeyFilled : credentialFilled));

  const credentialHint = isEdit
    ? provider?.has_credential
      ? t("credentialHintKeep")
      : t("credentialHintNone")
    : t("credentialHintCreate");

  const secretPlaceholder = isEdit && provider?.has_credential ? t("secretConfigured") : "";

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogContent data-testid="provider-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("formEditTitle") : t("formCreateTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="form-kind">{t("colKind")}</Label>
              <Select
                id="form-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as ProviderKind)}
                disabled={isEdit}
                data-testid="form-kind"
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-display-name">{t("colName")}</Label>
              <Input
                id="form-display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Claude (prod)"
                data-testid="form-display-name"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-slug">{t("fieldSlug")}</Label>
              <Input
                id="form-slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="ollama-local"
                data-testid="form-slug"
              />
              <p className="text-muted-foreground text-xs">
                {t("slugHintLead")} <code>ollama-local</code> vs <code>ollama-cloud</code>
                {t("slugHintTail")}
              </p>
            </div>
          </div>

          {/* base_url — required for azure_foundry + ollama, hidden for claude_sdk. */}
          {kind !== "claude_sdk" ? (
            <div className="space-y-1">
              <Label htmlFor="form-base-url">
                {kind === "azure_foundry"
                  ? t("endpointApim")
                  : kind === "ollama"
                    ? t("endpointOllama")
                    : t("endpoint")}
                {needsBaseUrl ? " *" : ""}
              </Label>
              <Input
                id="form-base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={
                  kind === "azure_foundry"
                    ? "https://apim.example.com/openai"
                    : "http://localhost:11434"
                }
                data-testid="form-base-url"
              />
            </div>
          ) : null}

          {/* Credential fields switch by kind (write-only). */}
          <div className="space-y-1" data-testid="form-credential-section">
            {kind === "claude_sdk" ? (
              <>
                <Label htmlFor="form-claude-auth-mode">{t("claudeAuthMode")}</Label>
                <select
                  id="form-claude-auth-mode"
                  data-testid="form-claude-auth-mode"
                  value={claudeAuthMode}
                  onChange={(e) =>
                    setClaudeAuthMode(
                      e.target.value === "subscription" ? "subscription" : "api_key",
                    )
                  }
                  className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                >
                  <option value="api_key">{t("claudeApiKeyOption")}</option>
                  <option value="subscription">{t("claudeSubscriptionOption")}</option>
                </select>
                <Label htmlFor="form-oauth-token">
                  {claudeAuthMode === "api_key"
                    ? t("claudeApiKeyLabel")
                    : t("claudeSubscriptionLabel")}
                  {isEdit ? "" : " *"}
                </Label>
                <Input
                  id="form-oauth-token"
                  type="password"
                  autoComplete="off"
                  value={oauthToken}
                  onChange={(e) => setOauthToken(e.target.value)}
                  placeholder={secretPlaceholder}
                  data-testid="form-oauth-token"
                />
              </>
            ) : kind === "copilot" ? (
              <>
                <Label htmlFor="form-oauth-token">{t("copilotTokenLabel")}</Label>
                <Input
                  id="form-oauth-token"
                  type="password"
                  autoComplete="off"
                  value={oauthToken}
                  onChange={(e) => setOauthToken(e.target.value)}
                  placeholder={secretPlaceholder}
                  data-testid="form-oauth-token"
                />
              </>
            ) : kind === "azure_foundry" ? (
              <>
                <Label htmlFor="form-api-key">
                  {t("azureApiKeyLabel")}
                  {isEdit ? "" : " *"}
                </Label>
                <Input
                  id="form-api-key"
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={secretPlaceholder}
                  data-testid="form-api-key"
                />
              </>
            ) : (
              <>
                <Label htmlFor="form-bearer-token">{t("ollamaBearerLabel")}</Label>
                <Input
                  id="form-bearer-token"
                  type="password"
                  autoComplete="off"
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  placeholder={secretPlaceholder}
                  data-testid="form-bearer-token"
                />
              </>
            )}
            <p className="text-muted-foreground text-xs" data-testid="form-credential-hint">
              {credentialHint}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="form-is-active"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              data-testid="form-is-active"
            />
            <Label htmlFor="form-is-active">{t("fieldActive")}</Label>
          </div>

          {saveMutation.isError ? (
            <p className="text-destructive text-xs" data-testid="provider-form-error">
              {errorText(saveMutation.error)}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="provider-form-cancel">
            {t("cancel")}
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!canSave || saveMutation.isPending}
            data-testid="provider-form-submit"
          >
            {saveMutation.isPending ? t("saving") : isEdit ? t("save") : t("submitCreate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
