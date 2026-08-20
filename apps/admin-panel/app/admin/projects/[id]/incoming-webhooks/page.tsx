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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { WebhookDialog } from "./webhook-dialog";
import {
  emptyConfigForm,
  ORIGIN_BADGE,
  type ActionMappingRule,
  type WebhookConfig,
  type WebhookConfigWithSecret,
  type WebhookDelivery,
} from "./webhook-types";

// The deployment base URL the external provider POSTs to. The backend
// returns a RELATIVE incoming_path; we prefix the API base so the operator
// sees the full URL to register.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectIncomingWebhooksPage() {
  const errorText = useErrorText();
  const t = useT("incomingWebhooks");
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
    if (!window.confirm(t("confirmDelete"))) return;
    deleteMutation.mutate(id);
  }

  function handleRotate(id: string) {
    if (!window.confirm(t("confirmRotate"))) return;
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
            {t("forbiddenBefore")} <strong>tenant_admin</strong> {t("forbiddenAfter")}
          </p>
        </div>
      }
    >
      <div
        className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
        data-testid="incoming-webhooks-page"
      >
        <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
        <PageHeader
          icon={<Webhook className="h-6 w-6 sm:h-7 sm:w-7" />}
          title={t("title")}
          description={t("description")}
          data-testid="incoming-webhooks-header"
          actions={
            <Button onClick={handleAdd} data-testid="webhook-add-button" disabled={busy}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              {t("add")}
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
          <p className="text-muted-foreground mt-6 text-sm">{t("loading")}</p>
        ) : listQuery.isError ? (
          <p className="text-destructive mt-6 text-sm" data-testid="incoming-webhooks-error">
            {errorText(listQuery.error)}
          </p>
        ) : configs.length === 0 ? (
          <Card className="mt-6">
            <CardContent className="py-10 text-center">
              <p
                className="text-muted-foreground text-sm italic"
                data-testid="incoming-webhooks-empty"
              >
                {t("emptyBefore")} <strong>“{t("add")}”</strong> {t("emptyAfter")}
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
              return errorText(err);
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
  const t = useT("incomingWebhooks");
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
          <p className="text-sm font-semibold">{t("secretTitle", { name })}</p>
          <p className="mt-1 text-xs">
            {t("secretHintBefore")} <strong>{t("secretHintStrong")}</strong>
            {t("secretHintAfter")}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onDismiss}
          data-testid="webhook-secret-dismiss"
          aria-label={t("close")}
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
          {copied ? t("copied") : t("copy")}
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
  const t = useT("incomingWebhooks");
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
                {t("badgeEnabled")}
              </Badge>
            ) : (
              <Badge variant="muted" data-testid={`webhook-disabled-${config.id}`}>
                {t("badgeDisabled")}
              </Badge>
            )}
          </CardTitle>
          <p className="text-muted-foreground mt-1 break-all font-mono text-xs">
            <span data-testid={`webhook-url-${config.id}`}>{fullUrl}</span>
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {config.action_mappings.length === 1
              ? t("mappingCountOne")
              : t("mappingCountMany", { n: config.action_mappings.length })}{" "}
            · {t("lastDelivery")}{" "}
            {config.last_event_at ? new Date(config.last_event_at).toLocaleString() : t("never")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onEdit}
            disabled={busy}
            data-testid={`webhook-edit-${config.id}`}
            aria-label={t("edit")}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onRotate}
            disabled={busy}
            data-testid={`webhook-rotate-${config.id}`}
            aria-label={t("rotate")}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onDelete}
            disabled={busy}
            data-testid={`webhook-delete-${config.id}`}
            aria-label={t("delete")}
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
          {showDeliveries ? t("hideDeliveries") : t("showDeliveries")}
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
  const errorText = useErrorText();
  const t = useT("incomingWebhooks");
  const query = useQuery({
    queryKey: ["incoming-webhook-deliveries", projectId, configId],
    queryFn: () =>
      apiFetch<WebhookDelivery[]>(
        `/projects/${projectId}/incoming-webhooks/${configId}/deliveries`,
      ),
    refetchOnWindowFocus: false,
  });

  if (query.isLoading) {
    return <p className="text-muted-foreground mt-2 text-xs">{t("loadingDeliveries")}</p>;
  }
  if (query.isError) {
    return (
      <p
        className="text-destructive mt-2 text-xs"
        data-testid={`webhook-deliveries-error-${configId}`}
      >
        {errorText(query.error)}
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
        {t("deliveriesEmpty")}
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
            {d.verified ? t("verified") : t("rejected")}
          </Badge>
          <code className="font-mono">{d.event_type ?? t("noEventType")}</code>
          <span className="text-muted-foreground">{new Date(d.received_at).toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}
