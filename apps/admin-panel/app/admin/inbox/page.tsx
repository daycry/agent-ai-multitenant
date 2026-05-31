"use client";

/**
 * Bandeja personal "Tareas asignadas a mí" (Plan 16 task_16_08).
 *
 * Vista personal de CUALQUIER usuario: lista sus HumanTaskAssignments activas
 * (status assigned / accepted) con la tarea, proyecto, plan y la fecha límite de
 * aceptación, más botones de acción contextual:
 *   - Aceptar           (pending_acceptance -> accepted; tarea -> in_progress)
 *   - Rechazar          (con justificación obligatoria; tarea -> blocked)
 *   - Marcar completada (accepted -> tarea in_review)
 *   - Escalar al admin  (tarea -> blocked + notifica al admin del tenant)
 *
 * Permisos: el backend es la fuente de verdad. La bandeja muestra SOLO las
 * asignaciones del propio usuario que la consulta (filtro por
 * assigned_to_user_id en el servidor + RLS por tenant). No es admin-only: es la
 * bandeja personal de quien esté logueado.
 *
 * Dos pestañas (Tabs):
 *   - "Activas"   — las asignaciones activas con sus acciones contextuales.
 *   - "Histórico" — tareas pasadas + métricas personales (task_16_10).
 *
 * Endpoints (routers/human_inbox.py):
 *   GET  /inbox/assignments
 *   POST /inbox/assignments/{id}/accept
 *   POST /inbox/assignments/{id}/reject      { justification }
 *   POST /inbox/assignments/{id}/complete    { comments? }
 *   POST /inbox/assignments/{id}/escalate    { justification? }
 *   GET  /inbox/history                      (task_16_10)
 *   GET  /inbox/metrics                      (task_16_10)
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock,
  FolderKanban,
  History,
  Home,
  Inbox,
  ListChecks,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiFetch } from "@/lib/api";

import { HistoryTab } from "./history-tab";
import { InboxJustifyDialog } from "./justify-dialog";
import { InboxSubmitDialog } from "./submit-dialog";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.human_inbox
// ---------------------------------------------------------------------------
export interface InboxAssignment {
  assignment_id: string;
  task_id: string;
  human_agent_id: string | null;
  assignment_status: string;
  task_status: string;
  assigned_at: string;
  acceptance_deadline: string | null;
  task_title: string;
  task_description: string | null;
  project_id: string;
  project_name: string | null;
  plan_id: string | null;
  plan_title: string | null;
}

interface ActionResult {
  assignment_id: string;
  task_id: string;
  action: string;
  assignment_status: string;
  task_status: string;
}

// The task §7.2 statuses an inbox row can be in, mapped to a badge meaning.
const TASK_STATUS_LABEL: Record<string, string> = {
  assigned_to_human: "Asignada",
  in_progress: "En curso",
  in_review: "En revisión",
};

const TASK_STATUS_VARIANT: Record<string, BadgeVariant> = {
  assigned_to_human: "warning",
  in_progress: "info",
  in_review: "primary",
};

function apiErrorBody(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

/** Human-friendly relative deadline ("en 5 h", "vencida") for a pending row. */
function deadlineLabel(deadline: string | null): { text: string; overdue: boolean } | null {
  if (!deadline) return null;
  const ms = new Date(deadline).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return { text: "Plazo de aceptación vencido", overdue: true };
  const hours = Math.round(ms / (1000 * 60 * 60));
  if (hours < 1) return { text: "Aceptar en menos de 1 h", overdue: false };
  if (hours < 48) return { text: `Aceptar en ~${hours} h`, overdue: false };
  return { text: `Aceptar en ~${Math.round(hours / 24)} días`, overdue: false };
}

// ---------------------------------------------------------------------------
// One assignment row
// ---------------------------------------------------------------------------
function AssignmentCard({
  item,
  onAccept,
  onComplete,
  onReject,
  onEscalate,
  busy,
  actionError,
}: {
  item: InboxAssignment;
  onAccept: (a: InboxAssignment) => void;
  onComplete: (a: InboxAssignment) => void;
  onReject: (a: InboxAssignment) => void;
  onEscalate: (a: InboxAssignment) => void;
  busy: boolean;
  actionError: string | null;
}) {
  const isPending = item.assignment_status === "pending_acceptance";
  const isAccepted = item.assignment_status === "accepted";
  const deadline = deadlineLabel(item.acceptance_deadline);

  return (
    <Card data-testid={`inbox-assignment-${item.assignment_id}`} className="flex flex-col">
      <CardContent className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold" title={item.task_title}>
              {item.task_title}
            </h3>
            {item.task_description && (
              <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                {item.task_description}
              </p>
            )}
          </div>
          <Badge
            variant={TASK_STATUS_VARIANT[item.task_status] ?? "muted"}
            data-testid={`inbox-status-${item.assignment_id}`}
          >
            {TASK_STATUS_LABEL[item.task_status] ?? item.task_status}
          </Badge>
        </div>

        <dl className="flex flex-col gap-1.5 text-xs">
          <div className="flex items-center gap-2">
            <FolderKanban className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
            <dd>
              {item.project_name ?? "Proyecto"}
              {item.plan_title ? (
                <span className="text-muted-foreground"> · {item.plan_title}</span>
              ) : null}
            </dd>
          </div>
          {deadline && (
            <div className="flex items-center gap-2">
              <Clock
                className={
                  deadline.overdue
                    ? "text-danger-soft-foreground h-3.5 w-3.5 shrink-0"
                    : "text-muted-foreground h-3.5 w-3.5 shrink-0"
                }
              />
              <dd
                className={deadline.overdue ? "text-danger-soft-foreground font-medium" : undefined}
                data-testid={`inbox-deadline-${item.assignment_id}`}
              >
                {deadline.text}
              </dd>
            </div>
          )}
        </dl>

        {actionError && (
          <p
            className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
            data-testid={`inbox-error-${item.assignment_id}`}
          >
            {actionError}
          </p>
        )}

        <div className="mt-auto flex flex-wrap gap-2 pt-2">
          {isPending && (
            <Button
              size="sm"
              onClick={() => onAccept(item)}
              disabled={busy}
              data-testid={`inbox-accept-${item.assignment_id}`}
            >
              <CheckCircle2 className="mr-1 h-4 w-4" />
              Aceptar
            </Button>
          )}
          {isPending && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onReject(item)}
              disabled={busy}
              data-testid={`inbox-reject-${item.assignment_id}`}
            >
              <XCircle className="mr-1 h-4 w-4" />
              Rechazar
            </Button>
          )}
          {isAccepted && (
            <Button
              size="sm"
              onClick={() => onComplete(item)}
              disabled={busy}
              data-testid={`inbox-complete-${item.assignment_id}`}
            >
              <ListChecks className="mr-1 h-4 w-4" />
              Marcar completada
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onEscalate(item)}
            disabled={busy}
            data-testid={`inbox-escalate-${item.assignment_id}`}
          >
            <ShieldAlert className="mr-1 h-4 w-4" />
            Escalar al admin
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

type DialogMode = "reject" | "escalate";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function InboxPage() {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<{ mode: DialogMode; item: InboxAssignment } | null>(null);
  // The full delivery form (task_16_09) — its own modal, separate from the
  // text-only reject/escalate justification dialog.
  const [submit, setSubmit] = useState<{ item: InboxAssignment } | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});

  const query = useQuery({
    queryKey: ["inbox", "assignments"],
    queryFn: () => apiFetch<InboxAssignment[]>("/inbox/assignments"),
    refetchOnWindowFocus: false,
  });

  const items = query.data ?? [];

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["inbox", "assignments"] });
  }

  function clearError(assignmentId: string) {
    setActionErrors((prev) => {
      if (!(assignmentId in prev)) return prev;
      const next = { ...prev };
      delete next[assignmentId];
      return next;
    });
  }

  // A single mutation for the body-less accept action; the dialog handles the
  // body-carrying ones (reject / complete / escalate) and calls refresh itself.
  const actionMutation = useMutation<
    ActionResult,
    ApiError,
    { item: InboxAssignment; action: "accept" }
  >({
    mutationFn: ({ item, action }) =>
      apiFetch<ActionResult>(`/inbox/assignments/${item.assignment_id}/${action}`, {
        method: "POST",
      }),
    onSuccess: (_data, { item }) => {
      clearError(item.assignment_id);
      refresh();
    },
    onError: (err, { item }) => {
      setActionErrors((prev) => ({ ...prev, [item.assignment_id]: apiErrorBody(err) }));
    },
  });

  const busyId =
    actionMutation.isPending && actionMutation.variables
      ? actionMutation.variables.item.assignment_id
      : null;

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Mis tareas" },
        ]}
      />
      <PageHeader
        icon={<Inbox className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Tareas asignadas a mí"
        description="Las tareas humanas que tienes asignadas. Acéptalas, recházalas con una justificación, márcalas como completadas o escálalas a un administrador."
      />

      <InboxJustifyDialog
        request={dialog}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        onDone={() => {
          setDialog(null);
          refresh();
        }}
      />

      <InboxSubmitDialog
        request={submit}
        onOpenChange={(open) => {
          if (!open) setSubmit(null);
        }}
        onDone={() => {
          setSubmit(null);
          refresh();
        }}
      />

      <Tabs defaultValue="active" className="mt-6">
        <TabsList data-testid="inbox-tabs">
          <TabsTrigger value="active" data-testid="inbox-tab-active">
            <ListChecks className="mr-1.5 h-4 w-4" />
            Activas
          </TabsTrigger>
          <TabsTrigger value="history" data-testid="inbox-tab-history">
            <History className="mr-1.5 h-4 w-4" />
            Histórico
          </TabsTrigger>
        </TabsList>

        <TabsContent value="active">
          {query.isLoading && (
            <p className="text-muted-foreground text-sm" data-testid="inbox-loading">
              Cargando tus tareas…
            </p>
          )}
          {query.isError && (
            <Card className="border-destructive p-4" data-testid="inbox-error">
              <p className="text-destructive text-sm">
                No se pudieron cargar tus tareas: {apiErrorBody(query.error)}
              </p>
            </Card>
          )}
          {!query.isLoading && !query.isError && items.length === 0 && (
            <Card className="p-8" data-testid="inbox-empty">
              <div className="text-muted-foreground flex flex-col items-center gap-2 text-center text-sm">
                <Inbox className="h-8 w-8 opacity-50" />
                <p>No tienes tareas asignadas ahora mismo.</p>
                <p className="text-xs">Cuando te asignen una tarea humana, aparecerá aquí.</p>
              </div>
            </Card>
          )}
          {items.length > 0 && (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="inbox-grid">
              {items.map((item) => (
                <AssignmentCard
                  key={item.assignment_id}
                  item={item}
                  busy={busyId === item.assignment_id}
                  actionError={actionErrors[item.assignment_id] ?? null}
                  onAccept={(a) => actionMutation.mutate({ item: a, action: "accept" })}
                  onReject={(a) => setDialog({ mode: "reject", item: a })}
                  onComplete={(a) => setSubmit({ item: a })}
                  onEscalate={(a) => setDialog({ mode: "escalate", item: a })}
                />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="history">
          <HistoryTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
