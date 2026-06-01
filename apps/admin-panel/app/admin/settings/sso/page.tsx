"use client";

/**
 * task_08_03 — UI de configuración OIDC (SSO) por tenant.
 *
 * Un `tenant_admin` configura aquí el inicio de sesión único OIDC del
 * tenant: elige una plantilla de proveedor (Azure AD, Google, Okta,
 * Auth0, GitHub, GitLab, Apple, Facebook), rellena client_id + secreto y
 * los campos específicos del IdP (issuer, parámetros como el tenant de
 * Azure o el dominio de Okta), activa/desactiva el flag `enabled`, y ve
 * la URL de callback/redirect que debe registrar en el IdP.
 *
 * Se AÑADE junto al login local (email+contraseña): activar SSO no
 * rompe el login local. El backend guarda el secreto cifrado en reposo
 * (o referenciado en Vault) y NUNCA lo devuelve — la UI solo sabe si hay
 * secreto configurado (`has_client_secret` + `client_secret_source`).
 *
 * Por la restricción única (tenant_id, provider) hay como mucho UNA
 * config OIDC por tenant, así que la pantalla gestiona una sola entrada:
 * crear / editar / activar / borrar.
 *
 * Endpoints backend (routers/sso.py, RBAC tenant_admin + RLS):
 *   GET    /auth/sso/config            — lista (0 o 1) — sin el secreto
 *   POST   /auth/sso/config            — crear
 *   PUT    /auth/sso/config/{id}       — editar (secreto opcional)
 *   DELETE /auth/sso/config/{id}       — borrar (soft delete)
 *   GET    /auth/sso/oidc/templates    — plantillas por IdP
 *   GET    /auth/sso/oidc/callback-url — URL a registrar en el IdP
 *
 * Permisos: LEER cualquier miembro del tenant; ESCRIBIR solo
 * `tenant_admin` (el backend devuelve 403). Envolvemos las acciones de
 * mutación en <RoleGuard min="tenant_admin">.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Pencil, Plus, Shield, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.sso
// --------------------------------------------------------------------------
type SecretSource = "vault" | "encrypted";

interface SsoConfig {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
  has_client_secret: boolean;
  client_secret_source: SecretSource | null;
  created_at: string;
  updated_at: string;
}

interface OidcTemplate {
  template_id: string;
  display_name: string;
  issuer_template: string;
  default_scopes: string[];
  claim_mappings: Record<string, string>;
  required_params: string[];
  notes: string | null;
}

interface CallbackUrl {
  callback_url: string;
}

// Body of POST/PUT /auth/sso/config. `client_secret` is omitted on an
// edit that keeps the existing secret.
interface UpsertBody {
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  client_secret?: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
}

const SECRET_SOURCE_LABEL: Record<SecretSource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function SsoConfigPage() {
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ["sso-config"],
    queryFn: () => apiFetch<SsoConfig[]>("/auth/sso/config"),
    refetchOnWindowFocus: false,
  });

  const callbackQuery = useQuery({
    queryKey: ["sso-callback-url"],
    queryFn: () => apiFetch<CallbackUrl>("/auth/sso/oidc/callback-url"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SsoConfig | null>(null);

  // At most one OIDC config (unique constraint tenant_id+provider).
  const config = configQuery.data?.[0] ?? null;

  const saveMutation = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: UpsertBody }) =>
      id === null
        ? apiFetch<SsoConfig>("/auth/sso/config", { method: "POST", body })
        : apiFetch<SsoConfig>(`/auth/sso/config/${id}`, { method: "PUT", body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/sso/config/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ cfg, enabled }: { cfg: SsoConfig; enabled: boolean }) =>
      apiFetch<SsoConfig>(`/auth/sso/config/${cfg.id}`, {
        method: "PUT",
        body: {
          display_name: cfg.display_name,
          enabled,
          issuer: cfg.issuer,
          client_id: cfg.client_id,
          scopes: cfg.scopes,
          claim_mappings: cfg.claim_mappings,
        } satisfies UpsertBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
    },
  });

  function handleCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function handleEdit() {
    setEditing(config);
    setDialogOpen(true);
  }

  function handleDelete() {
    if (config === null) return;
    if (!window.confirm("¿Borrar la configuración OIDC de este tenant?")) return;
    deleteMutation.mutate(config.id);
  }

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="sso-config-page"
    >
      <PageHeader
        icon={<Shield className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="SSO empresarial (OIDC)"
        description="Inicio de sesión único por tenant. Se añade junto al login local — activarlo no lo reemplaza ni lo desactiva."
        data-testid="sso-config-header"
        actions={
          config === null ? (
            <RoleGuard min="tenant_admin">
              <Button onClick={handleCreate} data-testid="sso-create-button">
                <Plus className="mr-1 h-3.5 w-3.5" />
                Configurar OIDC
              </Button>
            </RoleGuard>
          ) : null
        }
      />

      <p className="text-muted-foreground mt-2 text-sm" data-testid="sso-saml-link">
        ¿Tu IdP habla SAML 2.0 en lugar de OIDC?{" "}
        <Link href="/admin/settings/sso/saml" className="text-primary underline">
          Configura SAML aquí
        </Link>
        .
      </p>

      <CallbackUrlCard
        url={callbackQuery.data?.callback_url ?? null}
        loading={callbackQuery.isLoading}
      />

      {configQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="sso-loading">
          Cargando…
        </p>
      ) : configQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="sso-load-error">
          {configQuery.error instanceof ApiError
            ? configQuery.error.body
            : String(configQuery.error)}
        </p>
      ) : config === null ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="sso-empty">
              Este tenant aún no tiene SSO configurado. Pulsa <strong>“Configurar OIDC”</strong>{" "}
              para conectarlo con tu proveedor de identidad.
            </p>
          </CardContent>
        </Card>
      ) : (
        <SsoConfigCard
          config={config}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggle={(enabled) => toggleMutation.mutate({ cfg: config, enabled })}
          busy={deleteMutation.isPending || toggleMutation.isPending}
        />
      )}

      {deleteMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="sso-delete-error">
          {deleteMutation.error instanceof ApiError
            ? deleteMutation.error.body
            : String(deleteMutation.error)}
        </p>
      ) : null}

      {dialogOpen ? (
        <SsoConfigDialog
          open={dialogOpen}
          onOpenChange={(next) => {
            if (!saveMutation.isPending) setDialogOpen(next);
          }}
          initial={editing}
          submitting={saveMutation.isPending}
          onSubmit={(body) => saveMutation.mutate({ id: editing?.id ?? null, body })}
          backendError={
            saveMutation.isError && saveMutation.error instanceof ApiError
              ? saveMutation.error.body
              : null
          }
        />
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------
// Callback URL card — the redirect URI to register at the IdP
// --------------------------------------------------------------------------
function CallbackUrlCard({ url, loading }: { url: string | null; loading: boolean }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (url === null) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore silently.
    }
  }

  return (
    <Card className="mt-6" data-testid="sso-callback-card">
      <CardHeader>
        <CardTitle className="text-base">URL de callback / redirect</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground mb-2 text-sm">
          Registra esta URL en la lista de redirect URIs permitidas de tu proveedor de identidad.
        </p>
        <div className="flex items-center gap-2">
          <code
            className="bg-muted/40 flex-1 break-all rounded-md border px-3 py-2 font-mono text-xs"
            data-testid="sso-callback-url"
          >
            {loading ? "Cargando…" : (url ?? "—")}
          </code>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={copy}
            disabled={url === null}
            data-testid="sso-callback-copy"
            aria-label="Copiar URL de callback"
          >
            <Copy className="h-3.5 w-3.5" />
            {copied ? "Copiado" : "Copiar"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Config card — the single OIDC config
// --------------------------------------------------------------------------
function SsoConfigCard({
  config,
  onEdit,
  onDelete,
  onToggle,
  busy,
}: {
  config: SsoConfig;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  return (
    <Card className="mt-6" data-testid="sso-config-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <span className="truncate">{config.display_name || "OIDC"}</span>
            <Badge variant={config.enabled ? "success" : "muted"} data-testid="sso-enabled-badge">
              {config.enabled ? "activo" : "inactivo"}
            </Badge>
            {config.has_client_secret ? (
              <Badge variant="info" data-testid="sso-secret-badge">
                <KeyRound className="mr-1 h-3 w-3" />
                secreto: {SECRET_SOURCE_LABEL[config.client_secret_source ?? "encrypted"]}
              </Badge>
            ) : (
              <Badge variant="warning" data-testid="sso-no-secret-badge">
                sin secreto
              </Badge>
            )}
          </CardTitle>
          <dl className="text-muted-foreground mt-2 space-y-1 text-xs">
            <div className="flex gap-2">
              <dt className="font-medium">Issuer:</dt>
              <dd className="break-all font-mono" data-testid="sso-config-issuer">
                {config.issuer}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Client ID:</dt>
              <dd className="break-all font-mono" data-testid="sso-config-client-id">
                {config.client_id}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Scopes:</dt>
              <dd className="font-mono" data-testid="sso-config-scopes">
                {config.scopes.join(" ")}
              </dd>
            </div>
          </dl>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <RoleGuard min="tenant_admin">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onToggle(!config.enabled)}
              disabled={busy}
              data-testid="sso-toggle-enabled"
            >
              {config.enabled ? "Desactivar" : "Activar"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={busy}
              data-testid="sso-edit-button"
              aria-label="Editar"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={busy}
              data-testid="sso-delete-button"
              aria-label="Eliminar"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </RoleGuard>
        </div>
      </CardHeader>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Dialog — create / edit one OIDC config
// --------------------------------------------------------------------------
interface FormState {
  display_name: string;
  issuer: string;
  client_id: string;
  client_secret: string;
  scopes: string;
  enabled: boolean;
}

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

function SsoConfigDialog({
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
          <DialogTitle>{isCreate ? "Configurar OIDC" : "Editar configuración OIDC"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* Template picker */}
            <div className="bg-muted/30 -mx-2 rounded-md border p-3">
              <Label htmlFor="sso-form-template">Plantilla de proveedor</Label>
              <Select
                id="sso-form-template"
                data-testid="sso-form-template"
                className="mt-1"
                value={templateId}
                onChange={(e) => applyTemplate(e.target.value)}
                disabled={templatesQuery.isLoading}
              >
                <option value="">
                  {templatesQuery.isLoading
                    ? "Cargando plantillas…"
                    : "— Elige un proveedor (opcional) —"}
                </option>
                {(templatesQuery.data ?? []).map((tpl) => (
                  <option key={tpl.template_id} value={tpl.template_id}>
                    {tpl.display_name}
                  </option>
                ))}
              </Select>
              <p className="text-muted-foreground mt-1.5 text-xs">
                Pre-rellena issuer, scopes y mapeo de claims con valores verificados. Después puedes
                ajustarlos manualmente.
              </p>
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
                    <Label htmlFor={`sso-form-param-${name}`}>Parámetro: {name}</Label>
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
              <Label htmlFor="sso-form-display-name">Nombre visible (opcional)</Label>
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
                El descubrimiento OIDC consulta{" "}
                <code>&lt;issuer&gt;/.well-known/openid-configuration</code>.
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
                Client secret{isCreate ? "" : " (dejar vacío para conservar el actual)"}
              </Label>
              <Input
                id="sso-form-client-secret"
                data-testid="sso-form-client-secret"
                type="password"
                autoComplete="new-password"
                value={state.client_secret}
                onChange={(e) => setState({ ...state, client_secret: e.target.value })}
                placeholder={isCreate ? "secreto del cliente OIDC" : "••••••••"}
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Se cifra en reposo antes de guardarse; el sistema nunca lo devuelve en claro.
              </p>
            </div>

            {/* Scopes */}
            <div>
              <Label htmlFor="sso-form-scopes">Scopes (separados por espacios)</Label>
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
              <span>
                Activar este proveedor en el login (añadido al login local, no lo reemplaza)
              </span>
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
            Cancelar
          </Button>
          <Button
            onClick={() => onSubmit(buildBody())}
            disabled={submitting || !canSubmit}
            data-testid="sso-form-submit"
          >
            {submitting ? "Guardando…" : isCreate ? "Crear" : "Guardar cambios"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
