"use client";

/**
 * task_08_06 — UI de configuración SAML 2.0 (SSO) por tenant.
 *
 * Un `tenant_admin` configura aquí el inicio de sesión único SAML del
 * tenant: pega/sube los metadatos XML del IdP (el backend extrae
 * entityId, URL de SSO y certificado), ajusta el formato de NameID y el
 * mapeo de atributos, activa/desactiva el flag `enabled`, y ve los
 * metadatos del SP (EntityID + ACS URL) que debe registrar en el IdP.
 *
 * Se AÑADE junto al login local (email+contraseña) y junto al SSO OIDC
 * de la Fase A: activar SAML no rompe ninguno de los otros. El backend
 * guarda la clave privada del SP cifrada en reposo (o referenciada en
 * Vault) y NUNCA la devuelve — la UI solo sabe si hay clave configurada
 * (`has_sp_private_key` + `sp_private_key_source`). El certificado del
 * IdP y el certificado público del SP no son secretos y sí circulan.
 *
 * Por la restricción única (tenant_id, provider) hay como mucho UNA
 * config SAML por tenant: crear / editar / activar / borrar.
 *
 * Endpoints backend (routers/sso.py, RBAC tenant_admin + RLS):
 *   GET    /auth/sso/saml/config              — lista (0 o 1) — sin la clave
 *   POST   /auth/sso/saml/config              — crear
 *   PUT    /auth/sso/saml/config/{id}         — editar (clave opcional)
 *   DELETE /auth/sso/saml/config/{id}         — borrar (soft delete)
 *   GET    /auth/sso/saml/sp-metadata         — EntityID + ACS del SP
 *   POST   /auth/sso/saml/parse-metadata      — parsear metadatos del IdP
 *
 * Permisos: LEER cualquier miembro del tenant; ESCRIBIR solo
 * `tenant_admin` (el backend devuelve 403). Envolvemos las acciones de
 * mutación en <RoleGuard min="tenant_admin">.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Pencil, Plus, Shield, Trash2, Upload } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.sso (SAML half)
// --------------------------------------------------------------------------
type KeySource = "vault" | "encrypted";

interface SamlConfig {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_mappings: Record<string, string>;
  sp_x509_cert: string | null;
  has_sp_private_key: boolean;
  sp_private_key_source: KeySource | null;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  created_at: string;
  updated_at: string;
}

interface SpMetadata {
  sp_entity_id: string;
  acs_url: string;
}

interface ParsedIdpMetadata {
  entity_id: string;
  sso_url: string;
  x509_cert: string;
  name_id_format: string | null;
}

// Body of POST/PUT /auth/sso/saml/config. `sp_private_key` is omitted on
// an edit that keeps the existing key.
interface UpsertBody {
  display_name: string | null;
  enabled: boolean;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_mappings: Record<string, string>;
  sp_x509_cert: string | null;
  sp_private_key?: string;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
}

const KEY_SOURCE_LABEL: Record<KeySource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// The closed NameID-format picker the UI offers (the API accepts any
// non-empty URN, but these cover the overwhelming majority of IdPs).
const NAME_ID_FORMATS: { value: string; label: string }[] = [
  {
    value: "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    label: "emailAddress (recomendado)",
  },
  {
    value: "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    label: "persistent",
  },
  {
    value: "urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
    label: "transient",
  },
  {
    value: "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
    label: "unspecified",
  },
];

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function SamlConfigPage() {
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ["saml-config"],
    queryFn: () => apiFetch<SamlConfig[]>("/auth/sso/saml/config"),
    refetchOnWindowFocus: false,
  });

  const metadataQuery = useQuery({
    queryKey: ["saml-sp-metadata"],
    queryFn: () => apiFetch<SpMetadata>("/auth/sso/saml/sp-metadata"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SamlConfig | null>(null);

  // At most one SAML config (unique constraint tenant_id+provider).
  const config = configQuery.data?.[0] ?? null;

  const saveMutation = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: UpsertBody }) =>
      id === null
        ? apiFetch<SamlConfig>("/auth/sso/saml/config", { method: "POST", body })
        : apiFetch<SamlConfig>(`/auth/sso/saml/config/${id}`, { method: "PUT", body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/sso/saml/config/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ cfg, enabled }: { cfg: SamlConfig; enabled: boolean }) =>
      apiFetch<SamlConfig>(`/auth/sso/saml/config/${cfg.id}`, {
        method: "PUT",
        body: {
          display_name: cfg.display_name,
          enabled,
          idp_entity_id: cfg.idp_entity_id,
          idp_sso_url: cfg.idp_sso_url,
          idp_x509_cert: cfg.idp_x509_cert,
          name_id_format: cfg.name_id_format,
          attribute_mappings: cfg.attribute_mappings,
          sp_x509_cert: cfg.sp_x509_cert,
          authn_requests_signed: cfg.authn_requests_signed,
          want_assertions_signed: cfg.want_assertions_signed,
          want_assertions_encrypted: cfg.want_assertions_encrypted,
          want_name_id_encrypted: cfg.want_name_id_encrypted,
        } satisfies UpsertBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
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
    if (!window.confirm("¿Borrar la configuración SAML de este tenant?")) return;
    deleteMutation.mutate(config.id);
  }

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="saml-config-page"
    >
      <PageHeader
        icon={<Shield className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="SSO empresarial (SAML 2.0)"
        description="Inicio de sesión único SAML por tenant. Se añade junto al login local y al SSO OIDC — activarlo no reemplaza ni desactiva ninguno."
        data-testid="saml-config-header"
        actions={
          config === null ? (
            <RoleGuard min="tenant_admin">
              <Button onClick={handleCreate} data-testid="saml-create-button">
                <Plus className="mr-1 h-3.5 w-3.5" />
                Configurar SAML
              </Button>
            </RoleGuard>
          ) : null
        }
      />

      <p className="text-muted-foreground mt-2 text-sm" data-testid="saml-oidc-link">
        ¿Tu IdP habla OIDC en lugar de SAML?{" "}
        <Link href="/admin/settings/sso" className="text-primary underline">
          Configura OIDC aquí
        </Link>
        .
      </p>

      <SpMetadataCard metadata={metadataQuery.data ?? null} loading={metadataQuery.isLoading} />

      {configQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="saml-loading">
          Cargando…
        </p>
      ) : configQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="saml-load-error">
          {configQuery.error instanceof ApiError
            ? configQuery.error.body
            : String(configQuery.error)}
        </p>
      ) : config === null ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="saml-empty">
              Este tenant aún no tiene SAML configurado. Pulsa <strong>“Configurar SAML”</strong>{" "}
              para conectarlo con tu proveedor de identidad.
            </p>
          </CardContent>
        </Card>
      ) : (
        <SamlConfigCard
          config={config}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggle={(enabled) => toggleMutation.mutate({ cfg: config, enabled })}
          busy={deleteMutation.isPending || toggleMutation.isPending}
        />
      )}

      {deleteMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="saml-delete-error">
          {deleteMutation.error instanceof ApiError
            ? deleteMutation.error.body
            : String(deleteMutation.error)}
        </p>
      ) : null}

      {dialogOpen ? (
        <SamlConfigDialog
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
// SP metadata card — EntityID + ACS URL to register at the IdP
// --------------------------------------------------------------------------
function SpMetadataCard({ metadata, loading }: { metadata: SpMetadata | null; loading: boolean }) {
  return (
    <Card className="mt-6" data-testid="saml-sp-metadata-card">
      <CardHeader>
        <CardTitle className="text-base">Metadatos del SP (este sistema)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          Registra estos valores en tu proveedor de identidad SAML: la <strong>Entity ID</strong>{" "}
          del SP y la <strong>URL de ACS</strong> (Assertion Consumer Service) a la que el IdP
          enviará la respuesta.
        </p>
        <CopyRow
          label="SP Entity ID"
          value={loading ? null : (metadata?.sp_entity_id ?? null)}
          testid="saml-sp-entity-id"
        />
        <CopyRow
          label="ACS URL"
          value={loading ? null : (metadata?.acs_url ?? null)}
          testid="saml-acs-url"
        />
      </CardContent>
    </Card>
  );
}

function CopyRow({
  label,
  value,
  testid,
}: {
  label: string;
  value: string | null;
  testid: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore silently.
    }
  }

  return (
    <div>
      <Label className="text-muted-foreground text-xs">{label}</Label>
      <div className="mt-1 flex items-center gap-2">
        <code
          className="bg-muted/40 flex-1 break-all rounded-md border px-3 py-2 font-mono text-xs"
          data-testid={testid}
        >
          {value ?? "Cargando…"}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={copy}
          disabled={value === null}
          data-testid={`${testid}-copy`}
          aria-label={`Copiar ${label}`}
        >
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Copiado" : "Copiar"}
        </Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Config card — the single SAML config
// --------------------------------------------------------------------------
function SamlConfigCard({
  config,
  onEdit,
  onDelete,
  onToggle,
  busy,
}: {
  config: SamlConfig;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  return (
    <Card className="mt-6" data-testid="saml-config-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="truncate">{config.display_name || "SAML"}</span>
            <Badge variant={config.enabled ? "success" : "muted"} data-testid="saml-enabled-badge">
              {config.enabled ? "activo" : "inactivo"}
            </Badge>
            {config.has_sp_private_key ? (
              <Badge variant="info" data-testid="saml-key-badge">
                <KeyRound className="mr-1 h-3 w-3" />
                clave SP: {KEY_SOURCE_LABEL[config.sp_private_key_source ?? "encrypted"]}
              </Badge>
            ) : (
              <Badge variant="muted" data-testid="saml-no-key-badge">
                sin clave SP
              </Badge>
            )}
            {config.authn_requests_signed ? (
              <Badge variant="info" data-testid="saml-signed-badge">
                AuthnRequest firmado
              </Badge>
            ) : null}
          </CardTitle>
          <dl className="text-muted-foreground mt-2 space-y-1 text-xs">
            <div className="flex gap-2">
              <dt className="font-medium">IdP Entity ID:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-entity-id">
                {config.idp_entity_id}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">SSO URL:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-sso-url">
                {config.idp_sso_url}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">NameID:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-name-id">
                {config.name_id_format}
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
              data-testid="saml-toggle-enabled"
            >
              {config.enabled ? "Desactivar" : "Activar"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={busy}
              data-testid="saml-edit-button"
              aria-label="Editar"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={busy}
              data-testid="saml-delete-button"
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
// Dialog — create / edit one SAML config
// --------------------------------------------------------------------------
interface FormState {
  display_name: string;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_email: string;
  attribute_full_name: string;
  sp_x509_cert: string;
  sp_private_key: string;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  enabled: boolean;
}

const DEFAULT_NAME_ID = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress";

function configToForm(config: SamlConfig | null): FormState {
  if (config === null) {
    return {
      display_name: "",
      idp_entity_id: "",
      idp_sso_url: "",
      idp_x509_cert: "",
      name_id_format: DEFAULT_NAME_ID,
      attribute_email: "",
      attribute_full_name: "",
      sp_x509_cert: "",
      sp_private_key: "",
      authn_requests_signed: false,
      want_assertions_signed: true,
      want_assertions_encrypted: false,
      want_name_id_encrypted: false,
      enabled: false,
    };
  }
  return {
    display_name: config.display_name ?? "",
    idp_entity_id: config.idp_entity_id,
    idp_sso_url: config.idp_sso_url,
    idp_x509_cert: config.idp_x509_cert,
    name_id_format: config.name_id_format,
    attribute_email: config.attribute_mappings.email ?? "",
    attribute_full_name: config.attribute_mappings.full_name ?? "",
    sp_x509_cert: config.sp_x509_cert ?? "",
    sp_private_key: "",
    authn_requests_signed: config.authn_requests_signed,
    want_assertions_signed: config.want_assertions_signed,
    want_assertions_encrypted: config.want_assertions_encrypted,
    want_name_id_encrypted: config.want_name_id_encrypted,
    enabled: config.enabled,
  };
}

function SamlConfigDialog({
  open,
  onOpenChange,
  initial,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: SamlConfig | null;
  submitting: boolean;
  onSubmit: (body: UpsertBody) => void;
  backendError: string | null;
}) {
  const isCreate = initial === null;
  const [state, setState] = useState<FormState>(() => configToForm(initial));
  const [metadataXml, setMetadataXml] = useState("");

  useEffect(() => {
    setState(configToForm(initial));
    setMetadataXml("");
  }, [initial]);

  const parseMutation = useMutation({
    mutationFn: (xml: string) =>
      apiFetch<ParsedIdpMetadata>("/auth/sso/saml/parse-metadata", {
        method: "POST",
        body: { metadata_xml: xml },
      }),
    onSuccess: (parsed) => {
      setState((s) => ({
        ...s,
        idp_entity_id: parsed.entity_id || s.idp_entity_id,
        idp_sso_url: parsed.sso_url || s.idp_sso_url,
        idp_x509_cert: parsed.x509_cert || s.idp_x509_cert,
        name_id_format: parsed.name_id_format || s.name_id_format,
      }));
    },
  });

  function buildBody(): UpsertBody {
    const attribute_mappings: Record<string, string> = {};
    if (state.attribute_email.trim()) attribute_mappings.email = state.attribute_email.trim();
    if (state.attribute_full_name.trim())
      attribute_mappings.full_name = state.attribute_full_name.trim();
    const body: UpsertBody = {
      display_name: state.display_name.trim() || null,
      enabled: state.enabled,
      idp_entity_id: state.idp_entity_id.trim(),
      idp_sso_url: state.idp_sso_url.trim(),
      idp_x509_cert: state.idp_x509_cert.trim(),
      name_id_format: state.name_id_format,
      attribute_mappings,
      sp_x509_cert: state.sp_x509_cert.trim() || null,
      authn_requests_signed: state.authn_requests_signed,
      want_assertions_signed: state.want_assertions_signed,
      want_assertions_encrypted: state.want_assertions_encrypted,
      want_name_id_encrypted: state.want_name_id_encrypted,
    };
    // Only send the SP key when the operator typed one. On edit, an empty
    // field means "keep the stored key".
    if (state.sp_private_key.trim()) {
      body.sp_private_key = state.sp_private_key;
    }
    return body;
  }

  const canSubmit =
    state.idp_entity_id.trim() !== "" &&
    state.idp_sso_url.trim() !== "" &&
    state.idp_x509_cert.trim() !== "";

  const parseError =
    parseMutation.isError && parseMutation.error instanceof ApiError
      ? parseMutation.error.body
      : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="saml-config-dialog">
        <DialogHeader>
          <DialogTitle>{isCreate ? "Configurar SAML" : "Editar configuración SAML"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* IdP metadata upload / paste */}
            <div className="bg-muted/30 -mx-2 rounded-md border p-3">
              <Label htmlFor="saml-form-metadata">Metadatos del IdP (XML)</Label>
              <textarea
                id="saml-form-metadata"
                data-testid="saml-form-metadata"
                className="border-input bg-background mt-1 h-28 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                value={metadataXml}
                onChange={(e) => setMetadataXml(e.target.value)}
                placeholder="Pega aquí el EntityDescriptor del IdP, o sube el archivo de metadatos…"
              />
              <div className="mt-2 flex items-center gap-2">
                <input
                  id="saml-form-metadata-file"
                  data-testid="saml-form-metadata-file"
                  type="file"
                  accept=".xml,text/xml,application/xml"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const text = await file.text();
                    setMetadataXml(text);
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="saml-form-metadata-upload"
                  onClick={() => document.getElementById("saml-form-metadata-file")?.click()}
                >
                  <Upload className="mr-1 h-3.5 w-3.5" />
                  Subir XML
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="saml-form-metadata-parse"
                  disabled={metadataXml.trim() === "" || parseMutation.isPending}
                  onClick={() => parseMutation.mutate(metadataXml)}
                >
                  {parseMutation.isPending ? "Analizando…" : "Extraer datos"}
                </Button>
              </div>
              <p className="text-muted-foreground mt-1.5 text-xs">
                Extrae automáticamente Entity ID, URL de SSO y certificado del IdP. Después puedes
                ajustarlos manualmente.
              </p>
              {parseError ? (
                <p className="text-destructive mt-2 text-xs" data-testid="saml-form-parse-error">
                  No se pudieron extraer los metadatos: {parseError}
                </p>
              ) : null}
            </div>

            {/* Display name */}
            <div>
              <Label htmlFor="saml-form-display-name">Nombre visible (opcional)</Label>
              <Input
                id="saml-form-display-name"
                data-testid="saml-form-display-name"
                value={state.display_name}
                onChange={(e) => setState({ ...state, display_name: e.target.value })}
                placeholder="Acme Okta"
              />
            </div>

            {/* IdP Entity ID */}
            <div>
              <Label htmlFor="saml-form-entity-id">IdP Entity ID</Label>
              <Input
                id="saml-form-entity-id"
                data-testid="saml-form-entity-id"
                value={state.idp_entity_id}
                onChange={(e) => setState({ ...state, idp_entity_id: e.target.value })}
                placeholder="https://idp.example.com/saml/metadata"
              />
            </div>

            {/* IdP SSO URL */}
            <div>
              <Label htmlFor="saml-form-sso-url">URL de SSO del IdP</Label>
              <Input
                id="saml-form-sso-url"
                data-testid="saml-form-sso-url"
                value={state.idp_sso_url}
                onChange={(e) => setState({ ...state, idp_sso_url: e.target.value })}
                placeholder="https://idp.example.com/saml/sso"
              />
            </div>

            {/* IdP signing cert */}
            <div>
              <Label htmlFor="saml-form-cert">Certificado de firma del IdP (X.509)</Label>
              <textarea
                id="saml-form-cert"
                data-testid="saml-form-cert"
                className="border-input bg-background mt-1 h-24 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                value={state.idp_x509_cert}
                onChange={(e) => setState({ ...state, idp_x509_cert: e.target.value })}
                placeholder="MIID… (cuerpo base64 del certificado o PEM completo)"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Con este certificado se verifica la firma de las aserciones del IdP.
              </p>
            </div>

            {/* NameID format */}
            <div>
              <Label htmlFor="saml-form-name-id">Formato de NameID</Label>
              <select
                id="saml-form-name-id"
                data-testid="saml-form-name-id"
                className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                value={state.name_id_format}
                onChange={(e) => setState({ ...state, name_id_format: e.target.value })}
              >
                {NAME_ID_FORMATS.map((fmt) => (
                  <option key={fmt.value} value={fmt.value}>
                    {fmt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Attribute mappings */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="saml-form-attr-email">Atributo de email (opcional)</Label>
                <Input
                  id="saml-form-attr-email"
                  data-testid="saml-form-attr-email"
                  value={state.attribute_email}
                  onChange={(e) => setState({ ...state, attribute_email: e.target.value })}
                  placeholder="email"
                />
              </div>
              <div>
                <Label htmlFor="saml-form-attr-full-name">Atributo de nombre (opcional)</Label>
                <Input
                  id="saml-form-attr-full-name"
                  data-testid="saml-form-attr-full-name"
                  value={state.attribute_full_name}
                  onChange={(e) => setState({ ...state, attribute_full_name: e.target.value })}
                  placeholder="displayName"
                />
              </div>
            </div>

            {/* SP cert + private key (optional, for signing/encryption) */}
            <div className="space-y-3 rounded-md border p-3">
              <p className="text-muted-foreground text-xs">
                Clave del SP — solo necesaria si firmas el AuthnRequest o cifras las aserciones.
              </p>
              <div>
                <Label htmlFor="saml-form-sp-cert">Certificado público del SP (X.509)</Label>
                <textarea
                  id="saml-form-sp-cert"
                  data-testid="saml-form-sp-cert"
                  className="border-input bg-background mt-1 h-20 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                  value={state.sp_x509_cert}
                  onChange={(e) => setState({ ...state, sp_x509_cert: e.target.value })}
                  placeholder="MIID… (certificado público del SP)"
                />
              </div>
              <div>
                <Label htmlFor="saml-form-sp-key">
                  Clave privada del SP (PEM)
                  {isCreate ? "" : " (dejar vacío para conservar la actual)"}
                </Label>
                <textarea
                  id="saml-form-sp-key"
                  data-testid="saml-form-sp-key"
                  className="border-input bg-background mt-1 h-20 w-full resize-y rounded-md border px-3 py-2 font-mono text-xs"
                  value={state.sp_private_key}
                  onChange={(e) => setState({ ...state, sp_private_key: e.target.value })}
                  placeholder={
                    isCreate ? "Pega aquí la clave privada del SP en formato PEM…" : "••••••••"
                  }
                />
                <p className="text-muted-foreground mt-1 text-xs">
                  Se cifra en reposo antes de guardarse; el sistema nunca la devuelve en claro.
                </p>
              </div>
            </div>

            {/* Security flags */}
            <div className="space-y-2">
              <CheckboxRow
                id="saml-form-authn-signed"
                checked={state.authn_requests_signed}
                onChange={(v) => setState({ ...state, authn_requests_signed: v })}
                label="Firmar el AuthnRequest saliente (requiere clave del SP)"
              />
              <CheckboxRow
                id="saml-form-want-assertions-signed"
                checked={state.want_assertions_signed}
                onChange={(v) => setState({ ...state, want_assertions_signed: v })}
                label="Exigir aserciones firmadas por el IdP (recomendado)"
              />
              <CheckboxRow
                id="saml-form-want-assertions-encrypted"
                checked={state.want_assertions_encrypted}
                onChange={(v) => setState({ ...state, want_assertions_encrypted: v })}
                label="Exigir aserciones cifradas (requiere clave del SP)"
              />
              <CheckboxRow
                id="saml-form-want-name-id-encrypted"
                checked={state.want_name_id_encrypted}
                onChange={(v) => setState({ ...state, want_name_id_encrypted: v })}
                label="Exigir NameID cifrado (requiere clave del SP)"
              />
            </div>

            {/* Enabled */}
            <CheckboxRow
              id="saml-form-enabled"
              checked={state.enabled}
              onChange={(v) => setState({ ...state, enabled: v })}
              label="Activar este proveedor en el login (añadido al login local y a OIDC, no los reemplaza)"
            />

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="saml-form-backend-error"
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
            data-testid="saml-form-cancel"
          >
            Cancelar
          </Button>
          <Button
            onClick={() => onSubmit(buildBody())}
            disabled={submitting || !canSubmit}
            data-testid="saml-form-submit"
          >
            {submitting ? "Guardando…" : isCreate ? "Crear" : "Guardar cambios"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckboxRow({
  id,
  checked,
  onChange,
  label,
}: {
  id: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-2 text-sm" htmlFor={id}>
      <input
        id={id}
        data-testid={id}
        type="checkbox"
        className="h-4 w-4 rounded border"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
