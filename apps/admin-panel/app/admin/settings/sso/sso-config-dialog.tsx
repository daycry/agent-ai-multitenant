"use client";

/**
 * Alta y edición de la configuración OIDC.
 *
 * Dos reglas que parecen detalles y no lo son (las fija
 * `sso-config.test.tsx`):
 *
 *  - al EDITAR, el campo de secreto vacío significa «conserva el guardado», así
 *    que el body no lleva `client_secret`; enviarlo vacío lo borraría;
 *  - `claim_mappings` viaja tal cual entre ediciones, para no perder el claim de
 *    grupos que trajo la plantilla del IdP.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

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

import type { FormState, OidcTemplate, SsoConfig, UpsertBody } from "./sso-types";

// --------------------------------------------------------------------------
// Dialog — create / edit one OIDC config
// --------------------------------------------------------------------------
function configToForm(config: SsoConfig | null): FormState {
  if (config === null) {
    return {
      display_name: "",
      issuer: "",
      client_id: "",
      client_secret: "",
      scopes: "openid email profile",
      enabled: false,
    };
  }
  return {
    display_name: config.display_name ?? "",
    issuer: config.issuer,
    client_id: config.client_id,
    client_secret: "",
    scopes: config.scopes.join(" "),
    enabled: config.enabled,
  };
}

export function SsoConfigDialog({
  open,
  onOpenChange,
  initial,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: SsoConfig | null;
  submitting: boolean;
  onSubmit: (body: UpsertBody) => void;
  backendError: string | null;
}) {
  const t = useT("ssoOidc");
  const isCreate = initial === null;
  const [state, setState] = useState<FormState>(() => configToForm(initial));
  // claim_mappings carries over verbatim on edit; templates pre-fill it
  // on create. We keep it in its own state so editing fields doesn't drop
  // the IdP-specific group claim a template provided.
  const [claimMappings, setClaimMappings] = useState<Record<string, string>>(
    initial?.claim_mappings ?? { email: "email", full_name: "name" },
  );
  // Tenant-specific template params (e.g. Azure `tenant`, Okta `domain`).
  const [templateId, setTemplateId] = useState<string>("");
  const [params, setParams] = useState<Record<string, string>>({});

  useEffect(() => {
    setState(configToForm(initial));
    setClaimMappings(initial?.claim_mappings ?? { email: "email", full_name: "name" });
    setTemplateId("");
    setParams({});
  }, [initial]);

  const templatesQuery = useQuery({
    queryKey: ["sso-templates"],
    queryFn: () => apiFetch<OidcTemplate[]>("/auth/sso/oidc/templates"),
    enabled: open,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });

  const selectedTemplate = useMemo(
    () => (templatesQuery.data ?? []).find((t) => t.template_id === templateId) ?? null,
    [templatesQuery.data, templateId],
  );

  // Render the issuer pattern with the supplied params, e.g.
  // `https://login.microsoftonline.com/{tenant}/v2.0` -> the real issuer.
  function renderIssuer(template: OidcTemplate, p: Record<string, string>): string {
    return template.issuer_template.replace(/\{(\w+)\}/g, (_match, name: string) => p[name] ?? "");
  }

  function applyTemplate(id: string) {
    setTemplateId(id);
    const tpl = (templatesQuery.data ?? []).find((t) => t.template_id === id) ?? null;
    if (tpl === null) return;
    const freshParams: Record<string, string> = {};
    for (const name of tpl.required_params) freshParams[name] = "";
    setParams(freshParams);
    setState((s) => ({
      ...s,
      display_name: s.display_name || tpl.display_name,
      issuer: renderIssuer(tpl, freshParams),
      scopes: tpl.default_scopes.join(" "),
    }));
    setClaimMappings({ ...tpl.claim_mappings });
  }

  function setParam(name: string, value: string) {
    const next = { ...params, [name]: value };
    setParams(next);
    if (selectedTemplate !== null) {
      setState((s) => ({ ...s, issuer: renderIssuer(selectedTemplate, next) }));
    }
  }

  function buildBody(): UpsertBody {
    const scopes = state.scopes
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const body: UpsertBody = {
      display_name: state.display_name.trim() || null,
      enabled: state.enabled,
      issuer: state.issuer.trim(),
      client_id: state.client_id.trim(),
      scopes,
      claim_mappings: claimMappings,
    };
    // Only send the secret when the operator typed one. On edit, an empty
    // field means "keep the stored secret".
    if (state.client_secret.trim()) {
      body.client_secret = state.client_secret;
    }
    return body;
  }

  const canSubmit =
    state.issuer.trim() !== "" &&
    state.client_id.trim() !== "" &&
    // On create a secret is mandatory; on edit it's optional (keep stored).
    (!isCreate || state.client_secret.trim() !== "");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="sso-config-dialog">
        <DialogHeader>
          <DialogTitle>{isCreate ? t("configure") : t("dialogEditTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* Template picker */}
            <div className="bg-muted/30 -mx-2 rounded-md border p-3">
              <Label htmlFor="sso-form-template">{t("templateLabel")}</Label>
              <Select
                id="sso-form-template"
                data-testid="sso-form-template"
                className="mt-1"
                value={templateId}
                onChange={(e) => applyTemplate(e.target.value)}
                disabled={templatesQuery.isLoading}
              >
                <option value="">
                  {templatesQuery.isLoading ? t("templateLoading") : t("templateNone")}
                </option>
                {(templatesQuery.data ?? []).map((tpl) => (
                  <option key={tpl.template_id} value={tpl.template_id}>
                    {tpl.display_name}
                  </option>
                ))}
              </Select>
              <p className="text-muted-foreground mt-1.5 text-xs">{t("templateHelp")}</p>
              {selectedTemplate?.notes ? (
                <p
                  className="text-muted-foreground mt-2 text-xs italic"
                  data-testid="sso-form-template-notes"
                >
                  {selectedTemplate.notes}
                </p>
              ) : null}
            </div>

            {/* Template-specific params (Azure tenant, Okta domain, …) */}
            {selectedTemplate !== null && selectedTemplate.required_params.length > 0 ? (
              <div className="space-y-3" data-testid="sso-form-params">
                {selectedTemplate.required_params.map((name) => (
                  <div key={name}>
                    <Label htmlFor={`sso-form-param-${name}`}>{t("paramLabel", { name })}</Label>
                    <Input
                      id={`sso-form-param-${name}`}
                      data-testid={`sso-form-param-${name}`}
                      value={params[name] ?? ""}
                      onChange={(e) => setParam(name, e.target.value)}
                      placeholder={name}
                    />
                  </div>
                ))}
              </div>
            ) : null}

            {/* Display name */}
            <div>
              <Label htmlFor="sso-form-display-name">{t("displayNameLabel")}</Label>
              <Input
                id="sso-form-display-name"
                data-testid="sso-form-display-name"
                value={state.display_name}
                onChange={(e) => setState({ ...state, display_name: e.target.value })}
                placeholder="Acme Entra ID"
              />
            </div>

            {/* Issuer */}
            <div>
              <Label htmlFor="sso-form-issuer">Issuer</Label>
              <Input
                id="sso-form-issuer"
                data-testid="sso-form-issuer"
                value={state.issuer}
                onChange={(e) => setState({ ...state, issuer: e.target.value })}
                placeholder="https://login.microsoftonline.com/<tenant>/v2.0"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                {t("issuerHelp")} <code>&lt;issuer&gt;/.well-known/openid-configuration</code>.
              </p>
            </div>

            {/* Client ID */}
            <div>
              <Label htmlFor="sso-form-client-id">Client ID</Label>
              <Input
                id="sso-form-client-id"
                data-testid="sso-form-client-id"
                value={state.client_id}
                onChange={(e) => setState({ ...state, client_id: e.target.value })}
                placeholder="abc123-client-id"
              />
            </div>

            {/* Client secret */}
            <div>
              <Label htmlFor="sso-form-client-secret">
                Client secret{isCreate ? "" : t("secretKeepHint")}
              </Label>
              <Input
                id="sso-form-client-secret"
                data-testid="sso-form-client-secret"
                type="password"
                autoComplete="new-password"
                value={state.client_secret}
                onChange={(e) => setState({ ...state, client_secret: e.target.value })}
                placeholder={isCreate ? t("secretPlaceholder") : "••••••••"}
              />
              <p className="text-muted-foreground mt-1 text-xs">{t("secretHelp")}</p>
            </div>

            {/* Scopes */}
            <div>
              <Label htmlFor="sso-form-scopes">{t("scopesLabel")}</Label>
              <Input
                id="sso-form-scopes"
                data-testid="sso-form-scopes"
                value={state.scopes}
                onChange={(e) => setState({ ...state, scopes: e.target.value })}
                placeholder="openid email profile"
              />
            </div>

            {/* Enabled */}
            <label className="flex items-center gap-2 text-sm" htmlFor="sso-form-enabled">
              <Checkbox
                id="sso-form-enabled"
                data-testid="sso-form-enabled"
                checked={state.enabled}
                onChange={(e) => setState({ ...state, enabled: e.target.checked })}
              />
              <span>{t("enabledLabel")}</span>
            </label>

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="sso-form-backend-error"
              >
                {backendError}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid="sso-form-cancel"
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={() => onSubmit(buildBody())}
            disabled={submitting || !canSubmit}
            data-testid="sso-form-submit"
          >
            {submitting ? t("saving") : isCreate ? t("create") : t("saveChanges")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
