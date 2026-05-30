"use client";

/**
 * Plan 10 task_10_16 — Inbox in-app con histórico de notificaciones.
 *
 * Lista el histórico de `NotificationLog` del tenant del usuario (status
 * sent/failed/dead_letter/retrying/queued/delivered, canal, evento, fecha),
 * con marcador leído/no-leído por usuario, filtros y paginación. Una entrada
 * en `dead_letter` muestra un enlace para reintentar manualmente reutilizando
 * el endpoint de task_10_13 (`POST /notifications/logs/{id}/retry`).
 *
 * Permisos: el backend es la fuente de verdad (RBAC + RLS). La lectura del
 * inbox y el marcar-leído son por miembro del tenant (estado por usuario); el
 * reintento manual es tenant_admin only — su botón va envuelto en <RoleGuard>.
 * Cada Tenant Admin tiene un inbox independiente (el marcador leído es por
 * usuario), y nunca ve notificaciones de otro tenant (RLS).
 *
 * Endpoints (routers/notifications.py):
 *   GET  /notifications/logs?limit&offset&status&channel_type&event_type&unread_only
 *   POST /notifications/logs/{id}/read
 *   POST /notifications/logs/read-all
 *   POST /notifications/logs/{id}/retry        (reutilizado de task_10_13)
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Inbox, RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.notifications
// --------------------------------------------------------------------------
interface NotificationLog {
  id: string;
  channel_id: string | null;
  event_type: string;
  channel_type: string;
  status: string;
  target: string | null;
  attempt: number;
  error: string | null;
  sent_at: string | null;
  created_at: string;
  read: boolean;
}

interface NotificationInbox {
  items: NotificationLog[];
  total: number;
  unread: number;
  limit: number;
  offset: number;
}

interface MarkReadResult {
  marked: number;
  unread: number;
}

interface RetryResult {
  log_id: string;
  status: string;
  source_log_id: string;
  attempt: number;
}

const PAGE_SIZE = 25;

// The lifecycle statuses a log can carry (api_server.db.notification).
const STATUSES = ["queued", "sent", "delivered", "failed", "retrying", "dead_letter"] as const;

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "info" | "muted"> = {
  sent: "success",
  delivered: "success",
  queued: "info",
  retrying: "warning",
  failed: "danger",
  dead_letter: "danger",
};

function apiErrorBody(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function NotificationInboxPage() {
  const queryClient = useQueryClient();

  const [statusFilter, setStatusFilter] = useState<string>("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(offset));
    if (statusFilter) params.set("status", statusFilter);
    if (unreadOnly) params.set("unread_only", "true");
    return params.toString();
  }, [statusFilter, unreadOnly, offset]);

  const inboxQuery = useQuery({
    queryKey: ["notification-inbox", queryString],
    queryFn: () => apiFetch<NotificationInbox>(`/notifications/logs?${queryString}`),
    refetchOnWindowFocus: false,
  });

  function invalidateInbox() {
    queryClient.invalidateQueries({ queryKey: ["notification-inbox"] });
  }

  const markReadMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<MarkReadResult>(`/notifications/logs/${id}/read`, { method: "POST" }),
    onSuccess: invalidateInbox,
  });

  const markAllMutation = useMutation({
    mutationFn: () => apiFetch<MarkReadResult>("/notifications/logs/read-all", { method: "POST" }),
    onSuccess: invalidateInbox,
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<RetryResult>(`/notifications/logs/${id}/retry`, { method: "POST" }),
    onSuccess: invalidateInbox,
  });

  const data = inboxQuery.data;
  const total = data?.total ?? 0;
  const unread = data?.unread ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  function changeStatus(next: string) {
    setStatusFilter(next);
    setOffset(0);
  }

  function toggleUnreadOnly() {
    setUnreadOnly((prev) => !prev);
    setOffset(0);
  }

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="notification-inbox-page"
    >
      <PageHeader
        icon={<Inbox className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Bandeja de notificaciones"
        description="Histórico de notificaciones enviadas a tus canales, con estado y reintento manual."
        data-testid="notification-inbox-header"
      />

      {/* Toolbar: filtros + marcar todo como leído */}
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Badge variant={unread > 0 ? "info" : "muted"} data-testid="inbox-unread-badge">
          {unread} sin leer
        </Badge>
        <label className="flex items-center gap-2 text-sm" htmlFor="inbox-status-filter">
          <span className="text-muted-foreground">Estado</span>
          <select
            id="inbox-status-filter"
            data-testid="inbox-status-filter"
            className="border-input bg-background h-9 rounded-md border px-2 text-sm"
            value={statusFilter}
            onChange={(e) => changeStatus(e.target.value)}
          >
            <option value="">Todos</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm" htmlFor="inbox-unread-only">
          <input
            id="inbox-unread-only"
            data-testid="inbox-unread-only"
            type="checkbox"
            className="h-4 w-4 rounded border"
            checked={unreadOnly}
            onChange={toggleUnreadOnly}
          />
          <span>Solo sin leer</span>
        </label>
        <div className="ml-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={() => markAllMutation.mutate()}
            disabled={markAllMutation.isPending || unread === 0}
            data-testid="inbox-mark-all-read"
          >
            <CheckCheck className="mr-1 h-3.5 w-3.5" />
            Marcar todo como leído
          </Button>
        </div>
      </div>

      {/* Lista */}
      {inboxQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="inbox-loading">
          Cargando…
        </p>
      ) : inboxQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="inbox-error">
          {apiErrorBody(inboxQuery.error)}
        </p>
      ) : (data?.items ?? []).length === 0 ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="inbox-empty">
              No hay notificaciones que coincidan con el filtro.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="mt-6 space-y-2" data-testid="inbox-list">
          {(data?.items ?? []).map((log) => (
            <InboxRow
              key={log.id}
              log={log}
              onMarkRead={() => markReadMutation.mutate(log.id)}
              onRetry={() => retryMutation.mutate(log.id)}
              markBusy={markReadMutation.isPending}
              retryBusy={retryMutation.isPending}
            />
          ))}
        </div>
      )}

      {retryMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="inbox-retry-error">
          {apiErrorBody(retryMutation.error)}
        </p>
      ) : null}

      {/* Paginación */}
      <div className="mt-6 flex items-center justify-between">
        <span className="text-muted-foreground text-xs" data-testid="inbox-count">
          {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} de {total}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={!hasPrev || inboxQuery.isFetching}
            data-testid="inbox-prev-page"
          >
            Anterior
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={!hasNext || inboxQuery.isFetching}
            data-testid="inbox-next-page"
          >
            Siguiente
          </Button>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Row
// --------------------------------------------------------------------------
function InboxRow({
  log,
  onMarkRead,
  onRetry,
  markBusy,
  retryBusy,
}: {
  log: NotificationLog;
  onMarkRead: () => void;
  onRetry: () => void;
  markBusy: boolean;
  retryBusy: boolean;
}) {
  const isDeadLetter = log.status === "dead_letter";
  return (
    <Card
      data-testid={`inbox-row-${log.id}`}
      data-read={log.read ? "true" : "false"}
      className={log.read ? "" : "border-l-primary border-l-4"}
    >
      <CardContent className="flex flex-wrap items-center gap-3 py-3">
        {!log.read ? (
          <span
            className="bg-primary inline-block h-2 w-2 shrink-0 rounded-full"
            aria-label="sin leer"
            data-testid={`inbox-unread-dot-${log.id}`}
          />
        ) : (
          <span className="inline-block h-2 w-2 shrink-0" aria-hidden="true" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs" data-testid={`inbox-event-${log.id}`}>
              {log.event_type}
            </span>
            <Badge variant="info" data-testid={`inbox-channel-${log.id}`}>
              {log.channel_type}
            </Badge>
            <Badge
              variant={STATUS_VARIANT[log.status] ?? "muted"}
              data-testid={`inbox-status-${log.id}`}
            >
              {log.status}
            </Badge>
            {log.attempt > 1 ? (
              <span className="text-muted-foreground text-xs">intento {log.attempt}</span>
            ) : null}
          </div>
          {log.error ? (
            <p
              className="text-destructive mt-1 truncate text-xs"
              data-testid={`inbox-row-error-${log.id}`}
            >
              {log.error}
            </p>
          ) : null}
          <p className="text-muted-foreground mt-1 text-xs">{formatTimestamp(log.created_at)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {isDeadLetter ? (
            <RoleGuard min="tenant_admin">
              <Button
                variant="outline"
                size="sm"
                onClick={onRetry}
                disabled={retryBusy}
                data-testid={`inbox-retry-${log.id}`}
              >
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                Reintentar
              </Button>
            </RoleGuard>
          ) : null}
          {!log.read ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={onMarkRead}
              disabled={markBusy}
              data-testid={`inbox-mark-read-${log.id}`}
            >
              Marcar leído
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
