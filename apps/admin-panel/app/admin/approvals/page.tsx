"use client";

/**
 * task_02_25 + task_02_26 — Human-approval queue.
 *
 * The in-app notification feed for human_approval_policy: every pending
 * approval request a reviewer must act on. Each card carries
 * Approve / Reject buttons and an optional reason; resolving one
 * (`POST /approvals/{id}/resolve`) drops it from the list.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";

interface ApprovalRequest {
  id: string;
  execution_id: string;
  task_id: string;
  project_id: string;
  category: string;
  action: Record<string, unknown>;
  status: string;
  requested_at: string;
}

export default function ApprovalsPage() {
  const query = useQuery({
    queryKey: ["approvals"],
    queryFn: () => apiFetch<ApprovalRequest[]>("/approvals"),
    refetchOnWindowFocus: false,
  });

  const pending = query.data ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<ShieldCheck className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Aprobaciones"
        description="Solicitudes de aprobación humana pendientes. Aprueba o rechaza para que la ejecución continúe."
      />

      <div className="mb-4 flex items-center gap-2">
        <span className="text-sm font-medium">Pendientes</span>
        <Badge variant={pending.length > 0 ? "warning" : "muted"} data-testid="approval-count">
          {pending.length}
        </Badge>
      </div>

      {query.isLoading && <p className="text-muted-foreground text-sm">Cargando solicitudes…</p>}

      {query.isError && (
        <Card className="border-destructive p-4" data-testid="approvals-error">
          <p className="text-destructive text-sm">
            No se pudieron cargar las solicitudes:{" "}
            {query.error instanceof ApiError ? query.error.body : String(query.error)}
          </p>
        </Card>
      )}

      {query.data && pending.length === 0 && (
        <Card className="p-8 text-center" data-testid="approvals-empty">
          <p className="text-muted-foreground text-sm">
            No hay solicitudes de aprobación pendientes.
          </p>
        </Card>
      )}

      <ul className="space-y-3" data-testid="approvals-list">
        {pending.map((request) => (
          <li key={request.id}>
            <ApprovalCard request={request} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApprovalCard({ request }: { request: ApprovalRequest }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const resolve = useMutation({
    mutationFn: (approved: boolean) =>
      apiFetch(`/approvals/${request.id}/resolve`, {
        method: "POST",
        body: { approved, reason: reason.trim() || null },
      }),
    onSuccess: () => {
      setError(null);
      // The resolved request drops off the pending feed.
      return queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.body : String(err)),
  });

  return (
    <Card data-testid={`approval-card-${request.id}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base" data-testid={`approval-category-${request.id}`}>
            {request.category}
          </CardTitle>
          <Badge variant="warning">{request.status}</Badge>
        </div>
        <p className="text-muted-foreground text-xs">
          Solicitada {new Date(request.requested_at).toLocaleString()}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <pre className="bg-muted/40 overflow-x-auto rounded-md p-2 text-xs">
          {JSON.stringify(request.action, null, 2)}
        </pre>

        <textarea
          data-testid={`reason-${request.id}`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Motivo (opcional)"
          rows={2}
          className={cn(
            "border-input bg-background w-full rounded-md border px-3 py-2 text-sm",
            "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2",
          )}
        />

        {error && (
          <p className="text-destructive text-xs" data-testid={`approval-error-${request.id}`}>
            {error}
          </p>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            data-testid={`approve-${request.id}`}
            disabled={resolve.isPending}
            onClick={() => resolve.mutate(true)}
            className={cn(
              "bg-primary text-primary-foreground rounded-md px-4 py-2 text-sm font-medium",
              "transition-opacity hover:opacity-90 disabled:opacity-50",
            )}
          >
            Aprobar
          </button>
          <button
            type="button"
            data-testid={`reject-${request.id}`}
            disabled={resolve.isPending}
            onClick={() => resolve.mutate(false)}
            className={cn(
              "bg-danger-soft text-danger-soft-foreground rounded-md px-4 py-2 text-sm font-medium",
              "transition-opacity hover:opacity-90 disabled:opacity-50",
            )}
          >
            Rechazar
          </button>
        </div>
      </CardContent>
    </Card>
  );
}
