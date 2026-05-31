"use client";

/**
 * task_13_11 — Webhooks ENTRANTES configurados por proyecto.
 *
 * Configura los webhooks que herramientas externas (GitHub, Jira, Sentry,
 * Linear, GitLab) pueden enviar a ESTE proyecto. Es el inverso de los
 * webhooks salientes del Plan 10: aquí verificamos la firma HMAC que el
 * emisor estampa sobre SU payload, contra un secreto por-proyecto.
 *
 * Backend (RBAC tenant_admin, RLS tenant+proyecto):
 *   - GET    /projects/{id}/incoming-webhooks                  — listar configs
 *   - POST   /projects/{id}/incoming-webhooks                  — crear (devuelve el secreto UNA vez)
 *   - PUT    /projects/{id}/incoming-webhooks/{cid}            — editar nombre/enabled/mappings
 *   - POST   /projects/{id}/incoming-webhooks/{cid}/rotate-secret — rotar secreto (devuelve uno nuevo)
 *   - DELETE /projects/{id}/incoming-webhooks/{cid}            — soft-delete
 *   - GET    /projects/{id}/incoming-webhooks/{cid}/deliveries — entregas recientes (metadata)
 *
 * Seguridad de secretos (CLAUDE.md): el secreto de firma se muestra UNA
 * sola vez al crear/rotar (banner copiable) y nunca más — el listado solo
 * trae metadatos. Toda la pantalla va envuelta en <RoleGuard min="tenant_admin">.
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, RefreshCw, Trash2, Webhook, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
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
// Types (mirror api_server.schemas.incoming_webhooks)
// --------------------------------------------------------------------------
type Origin = "github" | "gitlab" | "jira" | "sentry" | "linear" | "generic";
type ActionKind = "create_task" | "comment" | "escalate";

interface ActionMappingRule {
  event_type: string;
  action: ActionKind;
  title_template: string | null;
  body_template: string | null;
  target_task_id: string | null;
}

interface WebhookConfig {
  id: string;
  project_id: string;
  origin: Origin;
  name: string;
  enabled: boolean;
  action_mappings: ActionMappingRule[];
  last_event_at: string | null;
  created_at: string;
  updated_at: string;
  incoming_path: string;
}

interface WebhookConfigWithSecret extends WebhookConfig {
  signing_secret: string;
}

interface WebhookDelivery {
  id: string;
  origin: string;
  delivery_id: string | null;
  event_type: string | null;
  verified: boolean;
  received_at: string;
}

const ORIGINS: { value: Origin; label: string }[] = [
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
  { value: "jira", label: "Jira" },
  { value: "sentry", label: "Sentry" },
  { value: "linear", label: "Linear" },
  { value: "generic", label: "Genérico (HMAC bare-hex)" },
];

const ORIGIN_BADGE: Record<Origin, BadgeVariant> = {
  github: "info",
  gitlab: "warning",
  jira: "info",
  sentry: "danger",
  linear: "muted",
  generic: "muted",
};

const ACTIONS: { value: ActionKind; label: string }[] = [
  { value: "create_task", label: "Crear tarea" },
  { value: "comment", label: "Comentar tarea" },
  { value: "escalate", label: "Escalar tarea" },
];

function emptyConfigForm(): {
  origin: Origin;
  name: string;
  enabled: boolean;
  action_mappings: ActionMappingRule[];
} {
  return { origin: "github", name: "", enabled: true, action_mappings: [] };
}

function emptyRule(): ActionMappingRule {
  return {
    event_type: "*",
    action: "create_task",
    title_template: null,
    body_template: null,
    target_task_id: null,
  };
}

// The deployment base URL the external provider POSTs to. The backend
// returns a RELATIVE incoming_path; we prefix the API base so the operator
// sees the full URL to register.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectIncomingWebhooksPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: ["incoming-webhooks", projectId],
    queryFn: () => apiFetch<WebhookConfig[]>(`/projects/${projectId}/incoming-webhooks`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<WebhookConfig | null>(null);
  // The clear secret returned by the last create/rotate — shown ONCE.
  const [revealedSecret, setRevealedSecret] = useState<{
    name: string;
    secret: string;
  } | null>(null);

  const configs = listQuery.data ?? [];

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["incoming-webhooks", projectId] });
  }

  const createMutation = useMutation({
    mutationFn: (form: ReturnType<typeof emptyConfigForm>) =>
      apiFetch<WebhookConfigWithSecret>(`/projects/${projectId}/incoming-webhooks`, {
        method: "POST",
        body: form,
      }),
    onSuccess: (created) => {
      invalidate();
      setDialogOpen(false);
      setEditing(null);
      setRevealedSecret({ name: created.name, secret: created.signing_secret });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<ActionMappingRule> | object }) =>
      apiFetch<WebhookConfig>(`/projects/${projectId}/incoming-webhooks/${id}`, {
        method: "PUT",
        body,
      }),
    onSuccess: () => {
      invalidate();
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<WebhookConfigWithSecret>(
        `/projects/${projectId}/incoming-webhooks/${id}/rotate-secret`,
        { method: "POST" },
      ),
    onSuccess: (rotated) => {
      invalidate();
      setRevealedSecret({ name: rotated.name, secret: rotated.signing_secret });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/projects/${projectId}/incoming-webhooks/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });

  function handleAdd() {
    setEditing(null);
    setDialogOpen(true);
  }

  function handleEdit(config: WebhookConfig) {
    setEditing(config);
    setDialogOpen(true);
  }

  function handleDelete(id: string) {
    if (!window.confirm("¿Borrar esta configuración de webhook entrante?")) return;
    deleteMutation.mutate(id);
  }

  function handleRotate(id: string) {
    if (
      !window.confirm(
        "Rotar el secreto invalida el actual de inmediato. Tendrás que actualizar el proveedor externo con el nuevo valor. ¿Continuar?",
      )
    )
      return;
    rotateMutation.mutate(id);
  }

  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    rotateMutation.isPending ||
    deleteMutation.isPending;

  return (
    <RoleGuard
      min="tenant_admin"
      fallback={
        <div
          className="mx-auto w-full max-w-6xl px-4 py-8"
          data-testid="incoming-webhooks-forbidden"
        >
          <p className="text-muted-foreground text-sm">
            Necesitas rol <strong>tenant_admin</strong> para gestionar webhooks entrantes.
          </p>
        </div>
      }
    >
      <div
        className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
        data-testid="incoming-webhooks-page"
      >
        <ProjectBreadcrumb projectId={projectId} current="Webhooks entrantes" />
        <PageHeader
          icon={<Webhook className="h-6 w-6 sm:h-7 sm:w-7" />}
          title="Webhooks entrantes del proyecto"
          description="Eventos que herramientas externas (GitHub, Jira, Sentry…) envían a este proyecto. Se verifica la firma HMAC antes de actuar."
          data-testid="incoming-webhooks-header"
          actions={
            <Button onClick={handleAdd} data-testid="webhook-add-button" disabled={busy}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              Añadir webhook
            </Button>
          }
        />

        {revealedSecret ? (
          <SecretBanner
            name={revealedSecret.name}
            secret={revealedSecret.secret}
            onDismiss={() => setRevealedSecret(null)}
          />
        ) : null}

        {listQuery.isLoading ? (
          <p className="text-muted-foreground mt-6 text-sm">Cargando…</p>
        ) : listQuery.isError ? (
          <p className="text-destructive mt-6 text-sm" data-testid="incoming-webhooks-error">
            {listQuery.error instanceof ApiError ? listQuery.error.body : String(listQuery.error)}
          </p>
        ) : configs.length === 0 ? (
          <Card className="mt-6">
            <CardContent className="py-10 text-center">
              <p
                className="text-muted-foreground text-sm italic"
                data-testid="incoming-webhooks-empty"
              >
                Este proyecto aún no acepta webhooks entrantes. Pulsa{" "}
                <strong>“Añadir webhook”</strong> para configurar el primero.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="mt-6 space-y-3">
            {configs.map((config) => (
              <WebhookCard
                key={config.id}
                projectId={projectId}
                config={config}
                onEdit={() => handleEdit(config)}
                onRotate={() => handleRotate(config.id)}
                onDelete={() => handleDelete(config.id)}
                busy={busy}
              />
            ))}
          </div>
        )}

        {(createMutation.isError || updateMutation.isError || rotateMutation.isError) && (
          <p className="text-destructive mt-3 text-xs" data-testid="incoming-webhooks-save-error">
            {(() => {
              const err = createMutation.error ?? updateMutation.error ?? rotateMutation.error;
              return err instanceof ApiError ? err.body : String(err);
            })()}
          </p>
        )}

        {dialogOpen ? (
          <WebhookDialog
            open={dialogOpen}
            onOpenChange={(next) => {
              if (!busy) setDialogOpen(next);
            }}
            initial={editing}
            submitting={createMutation.isPending || updateMutation.isPending}
            onSubmit={(form) => {
              if (editing) {
                updateMutation.mutate({
                  id: editing.id,
                  body: {
                    name: form.name,
                    enabled: form.enabled,
                    action_mappings: form.action_mappings,
                  },
                });
              } else {
                createMutation.mutate(form);
              }
            }}
            backendError={
              createMutation.error instanceof ApiError
                ? createMutation.error.body
                : updateMutation.error instanceof ApiError
                  ? updateMutation.error.body
                  : null
            }
          />
        ) : null}
      </div>
    </RoleGuard>
  );
}

// --------------------------------------------------------------------------
// Secret banner — shown ONCE after create/rotate
// --------------------------------------------------------------------------
function SecretBanner({
  name,
  secret,
  onDismiss,
}: {
  name: string;
  secret: string;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); the value is
      // selectable in the field regardless.
    }
  }

  return (
    <div
      className="bg-success-soft text-success-soft-foreground mt-6 rounded-md border border-success/30 p-4"
      data-testid="webhook-secret-banner"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">🔑 Secreto de firma para “{name}”</p>
          <p className="mt-1 text-xs">
            Cópialo ahora — <strong>no se volverá a mostrar</strong>. Pégalo en el secreto del
            webhook del proveedor externo para que firme sus eventos.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onDismiss}
          data-testid="webhook-secret-dismiss"
          aria-label="Cerrar"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Input
          readOnly
          value={secret}
          data-testid="webhook-secret-value"
          className="font-mono text-xs"
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button variant="outline" size="sm" onClick={copy} data-testid="webhook-secret-copy">
          {copied ? "Copiado" : "Copiar"}
        </Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Card — one webhook config
// --------------------------------------------------------------------------
function WebhookCard({
  projectId,
  config,
  onEdit,
  onRotate,
  onDelete,
  busy,
}: {
  projectId: string;
  config: WebhookConfig;
  onEdit: () => void;
  onRotate: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const [showDeliveries, setShowDeliveries] = useState(false);
  const fullUrl = `${API_BASE}${config.incoming_path}`;

  return (
    <Card data-testid={`webhook-card-${config.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <span className="truncate">{config.name}</span>
            <Badge variant={ORIGIN_BADGE[config.origin]}>{config.origin}</Badge>
            {config.enabled ? (
              <Badge variant="success" data-testid={`webhook-enabled-${config.id}`}>
                activo
              </Badge>
            ) : (
              <Badge variant="muted" data-testid={`webhook-disabled-${config.id}`}>
                desactivado
              </Badge>
            )}
          </CardTitle>
          <p className="text-muted-foreground mt-1 break-all font-mono text-xs">
            <span data-testid={`webhook-url-${config.id}`}>{fullUrl}</span>
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {config.action_mappings.length} mapeo
            {config.action_mappings.length === 1 ? "" : "s"} · última entrega:{" "}
            {config.last_event_at ? new Date(config.last_event_at).toLocaleString() : "nunca"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onEdit}
            disabled={busy}
            data-testid={`webhook-edit-${config.id}`}
            aria-label="Editar"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onRotate}
            disabled={busy}
            data-testid={`webhook-rotate-${config.id}`}
            aria-label="Rotar secreto"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onDelete}
            disabled={busy}
            data-testid={`webhook-delete-${config.id}`}
            aria-label="Eliminar"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <button
          type="button"
          onClick={() => setShowDeliveries((v) => !v)}
          data-testid={`webhook-deliveries-toggle-${config.id}`}
          className="text-muted-foreground hover:text-foreground text-xs font-medium underline-offset-2 hover:underline"
        >
          {showDeliveries ? "Ocultar entregas recientes" : "Ver entregas recientes"}
        </button>
        {showDeliveries ? <DeliveriesPanel projectId={projectId} configId={config.id} /> : null}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Recent deliveries panel
// --------------------------------------------------------------------------
function DeliveriesPanel({ projectId, configId }: { projectId: string; configId: string }) {
  const query = useQuery({
    queryKey: ["incoming-webhook-deliveries", projectId, configId],
    queryFn: () =>
      apiFetch<WebhookDelivery[]>(
        `/projects/${projectId}/incoming-webhooks/${configId}/deliveries`,
      ),
    refetchOnWindowFocus: false,
  });

  if (query.isLoading) {
    return <p className="text-muted-foreground mt-2 text-xs">Cargando entregas…</p>;
  }
  if (query.isError) {
    return (
      <p
        className="text-destructive mt-2 text-xs"
        data-testid={`webhook-deliveries-error-${configId}`}
      >
        {query.error instanceof ApiError ? query.error.body : String(query.error)}
      </p>
    );
  }
  const deliveries = query.data ?? [];
  if (deliveries.length === 0) {
    return (
      <p
        className="text-muted-foreground mt-2 text-xs italic"
        data-testid={`webhook-deliveries-empty-${configId}`}
      >
        Sin entregas todavía.
      </p>
    );
  }
  return (
    <ul
      className="border-muted bg-muted/30 mt-2 max-h-48 space-y-1 overflow-auto rounded border p-2 text-xs"
      data-testid={`webhook-deliveries-list-${configId}`}
    >
      {deliveries.map((d) => (
        <li key={d.id} className="flex items-center gap-2" data-testid={`webhook-delivery-${d.id}`}>
          <Badge variant={d.verified ? "success" : "danger"}>
            {d.verified ? "verificado" : "rechazado"}
          </Badge>
          <code className="font-mono">{d.event_type ?? "(sin tipo)"}</code>
          <span className="text-muted-foreground">{new Date(d.received_at).toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------
// Dialog form — create/edit one webhook config
// --------------------------------------------------------------------------
function WebhookDialog({
  open,
  onOpenChange,
  initial,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: WebhookConfig | null;
  submitting: boolean;
  onSubmit: (form: ReturnType<typeof emptyConfigForm>) => void;
  backendError: string | null;
}) {
  const isEdit = initial !== null;
  const [state, setState] = useState<ReturnType<typeof emptyConfigForm>>(() =>
    initial
      ? {
          origin: initial.origin,
          name: initial.name,
          enabled: initial.enabled,
          action_mappings: initial.action_mappings.map((r) => ({ ...r })),
        }
      : emptyConfigForm(),
  );

  function updateRule(index: number, patch: Partial<ActionMappingRule>) {
    setState((s) => ({
      ...s,
      action_mappings: s.action_mappings.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    }));
  }

  function addRule() {
    setState((s) => ({ ...s, action_mappings: [...s.action_mappings, emptyRule()] }));
  }

  function removeRule(index: number) {
    setState((s) => ({
      ...s,
      action_mappings: s.action_mappings.filter((_, i) => i !== index),
    }));
  }

  function handleSubmit() {
    // Normalise blank templates / target ids to null so the backend validator
    // treats them as absent (mirrors the rule contract).
    const cleaned: ActionMappingRule[] = state.action_mappings.map((r) => ({
      event_type: r.event_type.trim() || "*",
      action: r.action,
      title_template: r.title_template?.trim() ? r.title_template.trim() : null,
      body_template: r.body_template?.trim() ? r.body_template.trim() : null,
      target_task_id:
        r.action === "create_task"
          ? null
          : r.target_task_id?.trim()
            ? r.target_task_id.trim()
            : null,
    }));
    onSubmit({ ...state, name: state.name.trim(), action_mappings: cleaned });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="webhook-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar webhook entrante" : "Nuevo webhook entrante"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            <div>
              <Label htmlFor="webhook-form-origin">Origen</Label>
              <select
                id="webhook-form-origin"
                data-testid="webhook-form-origin"
                className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm disabled:opacity-60"
                value={state.origin}
                disabled={isEdit}
                onChange={(e) => setState({ ...state, origin: e.target.value as Origin })}
              >
                {ORIGINS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              {isEdit ? (
                <p className="text-muted-foreground mt-1 text-xs">
                  El origen no se puede cambiar tras crear (la URL pública lo incluye).
                </p>
              ) : null}
            </div>

            <div>
              <Label htmlFor="webhook-form-name">Nombre</Label>
              <Input
                id="webhook-form-name"
                data-testid="webhook-form-name"
                value={state.name}
                onChange={(e) => setState({ ...state, name: e.target.value })}
                placeholder="CI en acme/api"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="webhook-form-enabled"
                checked={state.enabled}
                onChange={(e) => setState({ ...state, enabled: e.target.checked })}
              />
              Activo (un webhook desactivado rechaza todos los eventos)
            </label>

            {/* Mappings editor */}
            <div className="border-t pt-3">
              <div className="mb-2 flex items-center justify-between">
                <Label>Mapeos evento → acción</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addRule}
                  data-testid="webhook-form-add-rule"
                >
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  Añadir mapeo
                </Button>
              </div>
              {state.action_mappings.length === 0 ? (
                <p
                  className="text-muted-foreground text-xs italic"
                  data-testid="webhook-form-rules-empty"
                >
                  Sin mapeos. Los eventos verificados se registran pero no disparan ninguna acción.
                </p>
              ) : (
                <ul className="space-y-3" data-testid="webhook-form-rules">
                  {state.action_mappings.map((rule, idx) => (
                    <li key={idx} className="bg-muted/30 rounded-md border p-3">
                      <div className="flex items-center gap-2">
                        <Input
                          aria-label="event_type"
                          data-testid={`webhook-form-rule-event-${idx}`}
                          value={rule.event_type}
                          onChange={(e) => updateRule(idx, { event_type: e.target.value })}
                          placeholder="github.pull_request_review"
                          className="flex-1"
                        />
                        <select
                          aria-label="action"
                          data-testid={`webhook-form-rule-action-${idx}`}
                          className="border-input bg-background h-10 rounded-md border px-2 text-sm"
                          value={rule.action}
                          onChange={(e) =>
                            updateRule(idx, { action: e.target.value as ActionKind })
                          }
                        >
                          {ACTIONS.map((a) => (
                            <option key={a.value} value={a.value}>
                              {a.label}
                            </option>
                          ))}
                        </select>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => removeRule(idx)}
                          data-testid={`webhook-form-rule-remove-${idx}`}
                          aria-label="Quitar mapeo"
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      {rule.action !== "create_task" ? (
                        <Input
                          aria-label="target_task_id"
                          data-testid={`webhook-form-rule-target-${idx}`}
                          value={rule.target_task_id ?? ""}
                          onChange={(e) => updateRule(idx, { target_task_id: e.target.value })}
                          placeholder="UUID de la tarea destino"
                          className="mt-2"
                        />
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="webhook-form-backend-error"
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
            data-testid="webhook-form-cancel"
          >
            Cancelar
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || !state.name.trim()}
            data-testid="webhook-form-submit"
          >
            {submitting ? "Guardando…" : isEdit ? "Guardar cambios" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
