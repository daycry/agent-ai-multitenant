"use client";

/**
 * task_11_2_05 — Pantalla 'Proveedores LLM' del System Admin (ADR 0028).
 *
 * Los proveedores LLM (los cuatro caminos cerrados del ADR 0021:
 * `claude_sdk` / `copilot` / `azure_foundry` / `ollama`) son
 * **platform-global** y los gestiona EXCLUSIVAMENTE el System Admin — no
 * tienen `tenant_id` ni RLS; el backend gatea cada endpoint con
 * `require_system_admin` sobre la sesión BYPASSRLS. Esta pantalla deja al
 * System Admin:
 *
 *   - Listar los proveedores con su `kind` + `display_name` + estado
 *     (`is_active`) + si tienen credencial guardada (`has_credential`).
 *   - "Probar conexión" por proveedor (POST `/{id}/test`) → ok/err
 *     clasificado, sin filtrar nunca el secreto.
 *   - Crear / editar un proveedor: los campos de credencial cambian según
 *     el `kind`. Los inputs de secreto son **write-only**: al editar nunca
 *     se muestra el valor — solo "configurado"; dejarlo vacío conserva el
 *     secreto actual en Vault.
 *   - Activar / desactivar (toggle) sin abrir el diálogo.
 *   - GitHub Copilot Device Flow: iniciar (start) → mostrar `user_code` +
 *     `verification_uri` + abrir enlace → hacer polling (poll) hasta que
 *     GitHub autorice; el token acuñado va SOLO a Vault (nunca a la UI).
 *
 * SECRETOS SOLO A VAULT (CLAUDE.md / ADR 0028 — innegociable): el endpoint
 * recibe la credencial como `SecretStr`, la escribe en Vault
 * (`platform/llm/<id>`) y persiste solo `secret_vault_path`. Ninguna
 * respuesta de la API devuelve jamás el valor del secreto; esta pantalla
 * NUNCA lo muestra (solo el booleano `has_credential`).
 *
 * Endpoints backend:
 *   GET    /admin/llm-providers                       — list (newest first)
 *   POST   /admin/llm-providers                       — create (201)
 *   GET    /admin/llm-providers/{id}                  — one
 *   PUT    /admin/llm-providers/{id}                  — update / rotar credencial
 *   DELETE /admin/llm-providers/{id}                  — delete + borra secret de Vault
 *   POST   /admin/llm-providers/{id}/test             — probar conexión
 *   POST   /admin/llm/copilot/device-flow/start       — iniciar device flow
 *   POST   /admin/llm/copilot/device-flow/poll        — un poll del device flow
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Cpu,
  ExternalLink,
  KeyRound,
  Pencil,
  PlugZap,
  Plus,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.llm_providers + db.llm_providers enums.
// ---------------------------------------------------------------------------
type ProviderKind = "claude_sdk" | "copilot" | "azure_foundry" | "ollama";

interface LlmProvider {
  id: string;
  kind: string;
  display_name: string;
  base_url: string | null;
  is_active: boolean;
  config: Record<string, unknown>;
  secret_vault_path: string | null;
  has_credential: boolean;
  created_at: string;
  updated_at: string;
}

// Mirror api_server.schemas.llm_providers.LLMProviderTestResponse +
// llm_providers.liveness.LivenessStatus.
interface ProviderTestResult {
  ok: boolean;
  status: string;
  detail: string;
}

// Mirror api_server.schemas.copilot_device_flow.{DeviceFlowStartResponse,DeviceFlowPollResponse}.
interface DeviceFlowStart {
  provider_id: string;
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

interface DeviceFlowPoll {
  status: string;
  authorized: boolean;
  interval: number | null;
}

const KINDS: ProviderKind[] = ["claude_sdk", "copilot", "azure_foundry", "ollama"];

const KIND_LABEL: Record<ProviderKind, string> = {
  claude_sdk: "Claude Agent SDK",
  copilot: "GitHub Copilot",
  azure_foundry: "Azure AI Foundry (APIM)",
  ollama: "Ollama",
};

const KIND_BADGE: Record<string, BadgeVariant> = {
  claude_sdk: "primary",
  copilot: "info",
  azure_foundry: "success",
  ollama: "warning",
};

// Classified liveness status → human (ES) label + badge variant.
const TEST_STATUS_LABEL: Record<string, string> = {
  ok: "conexión OK",
  auth_error: "error de autenticación",
  connection_error: "error de conexión",
  config_error: "configuración incompleta",
  upstream_error: "error del proveedor",
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

function isKind(value: string): value is ProviderKind {
  return (KINDS as string[]).includes(value);
}

// ===========================================================================
// Page
// ===========================================================================
export default function LlmProvidersPage() {
  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="llm-providers-page"
    >
      <PageHeader
        icon={<Cpu className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Proveedores LLM"
        description="Catálogo global de proveedores LLM (ADR 0021/0028). Configuración platform-global, solo System Admin. Las credenciales se guardan únicamente en Vault."
        data-testid="llm-providers-header"
      />
      {/* La pantalla completa es solo System Admin: el backend gatea con
          require_system_admin y la tabla no tiene tenant_id ni RLS. */}
      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6" data-testid="llm-providers-forbidden">
            <CardContent className="flex items-center gap-3 py-10">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">
                Esta sección es exclusiva del System Admin de la plataforma.
              </p>
            </CardContent>
          </Card>
        }
      >
        <LlmProvidersContent />
      </RoleGuard>
    </div>
  );
}

function LlmProvidersContent() {
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<LlmProvider | null>(null);
  const [deviceFlowTarget, setDeviceFlowTarget] = useState<LlmProvider | null>(null);
  // Per-provider test result, keyed by provider id.
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({});

  const listQuery = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => apiFetch<LlmProvider[]>("/admin/llm-providers"),
    refetchOnWindowFocus: false,
  });

  const testMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<ProviderTestResult>(`/admin/llm-providers/${id}/test`, { method: "POST" }),
    onSuccess: (result, id) => {
      setTestResults((prev) => ({ ...prev, [id]: result }));
    },
    onError: (err, id) => {
      setTestResults((prev) => ({
        ...prev,
        [id]: { ok: false, status: "upstream_error", detail: errorText(err) },
      }));
    },
  });

  // Quick active toggle (PUT is_active) without opening the dialog.
  const toggleMutation = useMutation({
    mutationFn: (p: LlmProvider) =>
      apiFetch<LlmProvider>(`/admin/llm-providers/${p.id}`, {
        method: "PUT",
        body: { is_active: !p.is_active },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/admin/llm-providers/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });

  const rows = listQuery.data ?? [];

  return (
    <>
      <div className="mt-6 flex justify-end">
        <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="provider-create-open">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Nuevo proveedor
        </Button>
      </div>

      <div className="mt-4">
        {listQuery.isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="providers-loading">
            Cargando proveedores…
          </p>
        ) : listQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="providers-error">
            {errorText(listQuery.error)}
          </p>
        ) : rows.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="providers-empty">
                No hay proveedores configurados. Crea el primero con &laquo;Nuevo proveedor&raquo;.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="overflow-x-auto rounded-xl border" data-testid="providers-table">
            <table className="w-full text-sm">
              <thead className="bg-muted text-muted-foreground">
                <tr className="text-left">
                  <th className="px-3 py-2 font-medium">Tipo</th>
                  <th className="px-3 py-2 font-medium">Nombre</th>
                  <th className="px-3 py-2 font-medium">Endpoint</th>
                  <th className="px-3 py-2 font-medium">Credencial</th>
                  <th className="px-3 py-2 font-medium">Estado</th>
                  <th className="px-3 py-2 font-medium">Conexión</th>
                  <th className="px-3 py-2 text-right font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => {
                  const kindLabel = isKind(p.kind) ? KIND_LABEL[p.kind] : p.kind;
                  const result = testResults[p.id];
                  const isTesting = testMutation.isPending && testMutation.variables === p.id;
                  return (
                    <tr key={p.id} className="border-t" data-testid={`provider-row-${p.id}`}>
                      <td className="px-3 py-2">
                        <Badge variant={KIND_BADGE[p.kind] ?? "muted"}>{kindLabel}</Badge>
                      </td>
                      <td className="px-3 py-2 font-medium">{p.display_name}</td>
                      <td className="px-3 py-2 font-mono text-xs">{p.base_url ?? "—"}</td>
                      <td className="px-3 py-2" data-testid={`provider-credential-${p.id}`}>
                        {p.has_credential ? (
                          <Badge variant="success">
                            <KeyRound className="mr-1 h-3 w-3" />
                            configurada
                          </Badge>
                        ) : (
                          <Badge variant="muted">sin credencial</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => toggleMutation.mutate(p)}
                          disabled={toggleMutation.isPending}
                          className="cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid={`provider-toggle-${p.id}`}
                          aria-label={p.is_active ? "Desactivar proveedor" : "Activar proveedor"}
                          aria-pressed={p.is_active}
                        >
                          {p.is_active ? (
                            <Badge variant="success">activo</Badge>
                          ) : (
                            <Badge variant="muted">inactivo</Badge>
                          )}
                        </button>
                      </td>
                      <td className="px-3 py-2" data-testid={`provider-test-cell-${p.id}`}>
                        {isTesting ? (
                          <span className="text-muted-foreground text-xs">probando…</span>
                        ) : result ? (
                          <span
                            className="inline-flex items-center gap-1 text-xs"
                            data-testid={`provider-test-result-${p.id}`}
                            data-ok={result.ok ? "true" : "false"}
                            title={result.detail}
                          >
                            {result.ok ? (
                              <CheckCircle2 className="text-success h-3.5 w-3.5" />
                            ) : (
                              <XCircle className="text-destructive h-3.5 w-3.5" />
                            )}
                            {TEST_STATUS_LABEL[result.status] ?? result.status}
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => testMutation.mutate(p.id)}
                            disabled={isTesting}
                            data-testid={`provider-test-${p.id}`}
                            aria-label="Probar conexión"
                          >
                            <PlugZap className="h-3.5 w-3.5" />
                          </Button>
                          {p.kind === "copilot" ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setDeviceFlowTarget(p)}
                              data-testid={`provider-device-flow-${p.id}`}
                              aria-label="Autorizar con GitHub (Device Flow)"
                            >
                              <KeyRound className="h-3.5 w-3.5" />
                            </Button>
                          ) : null}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setEditTarget(p)}
                            data-testid={`provider-edit-${p.id}`}
                            aria-label="Editar"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => deleteMutation.mutate(p.id)}
                            disabled={deleteMutation.isPending}
                            data-testid={`provider-delete-${p.id}`}
                            aria-label="Eliminar"
                          >
                            <Trash2 className="text-destructive h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {toggleMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="provider-toggle-error">
            {errorText(toggleMutation.error)}
          </p>
        ) : null}
        {deleteMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="provider-delete-error">
            {errorText(deleteMutation.error)}
          </p>
        ) : null}
      </div>

      {createOpen ? (
        <ProviderFormDialog
          mode="create"
          onClose={() => setCreateOpen(false)}
          onSaved={() => {
            setCreateOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}

      {editTarget ? (
        <ProviderFormDialog
          mode="edit"
          provider={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => {
            setEditTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}

      {deviceFlowTarget ? (
        <CopilotDeviceFlowDialog
          provider={deviceFlowTarget}
          onClose={() => setDeviceFlowTarget(null)}
          onAuthorized={() => {
            setDeviceFlowTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}
    </>
  );
}

// ===========================================================================
// Create / edit dialog — credential fields switch by kind.
//
// Secret discipline: credential inputs are WRITE-ONLY. On edit we never
// receive the secret value (the API never returns it); the field shows a
// "configured" hint and, left blank, the existing Vault secret is kept.
// ===========================================================================
interface ProviderFormDialogProps {
  mode: "create" | "edit";
  provider?: LlmProvider;
  onClose: () => void;
  onSaved: () => void;
}

function ProviderFormDialog({ mode, provider, onClose, onSaved }: ProviderFormDialogProps) {
  const isEdit = mode === "edit";

  // `kind` is immutable on edit (a kind change is a different provider).
  const [kind, setKind] = useState<ProviderKind>(
    provider && isKind(provider.kind) ? provider.kind : "claude_sdk",
  );
  const [displayName, setDisplayName] = useState(provider?.display_name ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [isActive, setIsActive] = useState(provider?.is_active ?? true);

  // Write-only credential inputs (one is meaningful per kind).
  const [oauthToken, setOauthToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [bearerToken, setBearerToken] = useState("");

  const needsBaseUrl = kind === "azure_foundry" || kind === "ollama";

  const saveMutation = useMutation({
    mutationFn: () => {
      const trimmedBase = baseUrl.trim();
      if (isEdit && provider) {
        // PUT: send only what changed; `kind` is immutable. A blank secret
        // input means "keep the current Vault secret" (omit it).
        const body: Record<string, unknown> = {
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
        display_name: displayName.trim(),
        base_url: trimmedBase === "" ? null : trimmedBase,
        is_active: isActive,
      };
      addCredential(body);
      return apiFetch<LlmProvider>("/admin/llm-providers", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  // Append the meaningful credential field for the kind, ONLY when filled.
  function addCredential(body: Record<string, unknown>): void {
    if (kind === "claude_sdk" || kind === "copilot") {
      if (oauthToken.trim() !== "") body.oauth_token = oauthToken;
    } else if (kind === "azure_foundry") {
      if (apiKey.trim() !== "") body.api_key = apiKey;
    } else if (kind === "ollama") {
      if (bearerToken.trim() !== "") body.bearer_token = bearerToken;
    }
  }

  // Per-kind required-fields gate (mirrors the backend validator).
  const credentialFilled =
    (kind === "claude_sdk" || kind === "copilot") && oauthToken.trim() !== "";
  const apiKeyFilled = kind === "azure_foundry" && apiKey.trim() !== "";
  const canSave =
    displayName.trim() !== "" &&
    // base_url required for azure_foundry + ollama.
    (!needsBaseUrl || baseUrl.trim() !== "") &&
    // On create the required credential must be present; on edit it may be
    // kept (already in Vault), so we don't force it.
    (isEdit || kind === "ollama" || (kind === "azure_foundry" ? apiKeyFilled : credentialFilled));

  const credentialHint = isEdit
    ? provider?.has_credential
      ? "Hay una credencial configurada. Déjalo vacío para conservarla; escribe un valor para rotarla."
      : "No hay credencial configurada. Escribe un valor para guardarla en Vault."
    : "Se guardará únicamente en Vault (nunca en la base de datos ni en respuestas de la API).";

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogContent data-testid="provider-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar proveedor" : "Nuevo proveedor"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="form-kind">Tipo</Label>
              <select
                id="form-kind"
                className="border-input bg-background flex h-10 w-full rounded-md border px-3 py-2 text-sm disabled:opacity-50"
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
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-display-name">Nombre</Label>
              <Input
                id="form-display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Claude (prod)"
                data-testid="form-display-name"
              />
            </div>
          </div>

          {/* base_url — required for azure_foundry + ollama, hidden for claude_sdk. */}
          {kind !== "claude_sdk" ? (
            <div className="space-y-1">
              <Label htmlFor="form-base-url">
                {kind === "azure_foundry"
                  ? "Endpoint APIM (gateway)"
                  : kind === "ollama"
                    ? "Endpoint Ollama"
                    : "Endpoint"}
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
            {kind === "claude_sdk" || kind === "copilot" ? (
              <>
                <Label htmlFor="form-oauth-token">
                  Token OAuth
                  {kind === "copilot" ? " (o usa el Device Flow desde la lista)" : ""}
                </Label>
                <Input
                  id="form-oauth-token"
                  type="password"
                  autoComplete="off"
                  value={oauthToken}
                  onChange={(e) => setOauthToken(e.target.value)}
                  placeholder={isEdit && provider?.has_credential ? "•••••••• (configurado)" : ""}
                  data-testid="form-oauth-token"
                />
              </>
            ) : kind === "azure_foundry" ? (
              <>
                <Label htmlFor="form-api-key">
                  API key (subscription APIM){isEdit ? "" : " *"}
                </Label>
                <Input
                  id="form-api-key"
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEdit && provider?.has_credential ? "•••••••• (configurado)" : ""}
                  data-testid="form-api-key"
                />
              </>
            ) : (
              <>
                <Label htmlFor="form-bearer-token">Bearer token (Ollama Cloud, opcional)</Label>
                <Input
                  id="form-bearer-token"
                  type="password"
                  autoComplete="off"
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  placeholder={isEdit && provider?.has_credential ? "•••••••• (configurado)" : ""}
                  data-testid="form-bearer-token"
                />
              </>
            )}
            <p className="text-muted-foreground text-xs" data-testid="form-credential-hint">
              {credentialHint}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              id="form-is-active"
              type="checkbox"
              className="h-4 w-4"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              data-testid="form-is-active"
            />
            <Label htmlFor="form-is-active">Proveedor activo</Label>
          </div>

          {saveMutation.isError ? (
            <p className="text-destructive text-xs" data-testid="provider-form-error">
              {errorText(saveMutation.error)}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="provider-form-cancel">
            Cancelar
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!canSave || saveMutation.isPending}
            data-testid="provider-form-submit"
          >
            {saveMutation.isPending ? "Guardando…" : isEdit ? "Guardar" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ===========================================================================
// Copilot Device Flow dialog.
//
// start → show user_code + verification_uri + open link → poll on the
// suggested interval until GitHub authorizes. The minted token never
// appears in the UI — it lands in Vault (the poll response only carries a
// status + authorized boolean).
// ===========================================================================
type DeviceFlowPhase = "idle" | "starting" | "polling" | "authorized" | "error";

interface CopilotDeviceFlowDialogProps {
  provider: LlmProvider;
  onClose: () => void;
  onAuthorized: () => void;
}

function CopilotDeviceFlowDialog({
  provider,
  onClose,
  onAuthorized,
}: CopilotDeviceFlowDialogProps) {
  const [phase, setPhase] = useState<DeviceFlowPhase>("idle");
  const [start, setStart] = useState<DeviceFlowStart | null>(null);
  const [pollStatus, setPollStatus] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string>("");
  // Hold the active poll timer so we can clear it on unmount / close.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  async function pollOnce(info: DeviceFlowStart, interval: number): Promise<void> {
    if (cancelledRef.current) return;
    try {
      const result = await apiFetch<DeviceFlowPoll>("/admin/llm/copilot/device-flow/poll", {
        method: "POST",
        body: {
          provider_id: provider.id,
          device_code: info.device_code,
          interval,
        },
      });
      if (cancelledRef.current) return;
      setPollStatus(result.status);
      if (result.authorized) {
        setPhase("authorized");
        return;
      }
      if (result.status === "expired" || result.status === "denied") {
        setErrorMessage(
          result.status === "expired"
            ? "El código expiró. Vuelve a iniciar el Device Flow."
            : "La autorización fue denegada en GitHub.",
        );
        setPhase("error");
        return;
      }
      // pending / slow_down → keep polling on the (possibly backed-off) interval.
      const next = result.interval ?? interval;
      timerRef.current = setTimeout(() => void pollOnce(info, next), next * 1000);
    } catch (err) {
      if (cancelledRef.current) return;
      setErrorMessage(errorText(err));
      setPhase("error");
    }
  }

  async function startFlow(): Promise<void> {
    setPhase("starting");
    setErrorMessage("");
    setPollStatus("");
    try {
      const info = await apiFetch<DeviceFlowStart>("/admin/llm/copilot/device-flow/start", {
        method: "POST",
        body: { provider_id: provider.id },
      });
      if (cancelledRef.current) return;
      setStart(info);
      setPhase("polling");
      timerRef.current = setTimeout(() => void pollOnce(info, info.interval), info.interval * 1000);
    } catch (err) {
      if (cancelledRef.current) return;
      setErrorMessage(errorText(err));
      setPhase("error");
    }
  }

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="md">
      <DialogContent data-testid="device-flow-dialog">
        <DialogHeader>
          <DialogTitle>Autorizar GitHub Copilot — {provider.display_name}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs">
            Inicia el Device Flow de GitHub: te mostraremos un código y un enlace. Tras autorizar en
            GitHub, el token se acuña y se guarda únicamente en Vault — nunca aparece aquí.
          </p>

          {phase === "idle" ? (
            <Button onClick={() => void startFlow()} data-testid="device-flow-start">
              Iniciar Device Flow
            </Button>
          ) : null}

          {phase === "starting" ? (
            <p className="text-muted-foreground text-sm" data-testid="device-flow-starting">
              Iniciando…
            </p>
          ) : null}

          {start && (phase === "polling" || phase === "authorized") ? (
            <div className="space-y-3" data-testid="device-flow-codes">
              <div className="space-y-1">
                <Label>Código de usuario</Label>
                <p
                  className="bg-muted rounded-md px-3 py-2 text-center font-mono text-lg tracking-widest"
                  data-testid="device-flow-user-code"
                >
                  {start.user_code}
                </p>
              </div>
              <a
                href={start.verification_uri}
                target="_blank"
                rel="noreferrer noopener"
                className="text-primary inline-flex items-center gap-1 text-sm underline"
                data-testid="device-flow-verification-link"
              >
                Abrir {start.verification_uri}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              {phase === "polling" ? (
                <p
                  className="text-muted-foreground flex items-center gap-2 text-sm"
                  data-testid="device-flow-polling"
                >
                  Esperando autorización en GitHub…
                  {pollStatus === "slow_down" ? (
                    <span className="text-xs italic">(GitHub pidió esperar más)</span>
                  ) : null}
                </p>
              ) : null}
            </div>
          ) : null}

          {phase === "authorized" ? (
            <div
              className="border-success/40 bg-success-soft flex items-center gap-2 rounded-lg border p-3"
              data-testid="device-flow-authorized"
            >
              <CheckCircle2 className="text-success h-4 w-4 shrink-0" />
              <p className="text-sm">
                Autorizado. El token de Copilot se guardó en Vault para este proveedor.
              </p>
            </div>
          ) : null}

          {phase === "error" ? (
            <p className="text-destructive text-sm" data-testid="device-flow-error">
              {errorMessage}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          {phase === "authorized" ? (
            <Button onClick={onAuthorized} data-testid="device-flow-done">
              Hecho
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={onClose} data-testid="device-flow-cancel">
                Cancelar
              </Button>
              {phase === "error" ? (
                <Button onClick={() => void startFlow()} data-testid="device-flow-retry">
                  Reintentar
                </Button>
              ) : null}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
