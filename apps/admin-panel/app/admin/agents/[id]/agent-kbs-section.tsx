"use client";

/**
 * Sub-sección "Knowledge Bases" del detalle de agente
 * (Plan 06.9 task_06_9_10).
 *
 * Muestra las KBs granteadas al agente template + permite grant/revoke
 * via `<KbCombobox>` y un botón por fila. Los built-in agents NO ven
 * el botón de Grant (el backend rechazaría con 403 igualmente — esto
 * evita el affordance engañoso).
 *
 * Endpoints consumidos:
 *   - GET    /agents/{id}/knowledge-bases    listar grants
 *   - POST   /agents/{id}/knowledge-bases    grant {kb_id}
 *   - DELETE /agents/{id}/knowledge-bases/{kb_id}   revoke
 *
 * Los tres existen tras Plan 06.9 Fase A.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Library, Plus, Trash2 } from "lucide-react";

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
import { KbCombobox } from "@/components/ui/kb-combobox";
import { StateBlock } from "@/components/shared/state-block";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

interface AgentKbRow {
  kb_id: string;
  name: string;
  description: string | null;
  embedding_model_id: string;
  granted_at: string | null;
  granted_by: string | null;
}

interface AgentKbsSectionProps {
  agentId: string;
  isReadOnly: boolean;
}

export function AgentKbsSection({ agentId, isReadOnly }: AgentKbsSectionProps) {
  const queryClient = useQueryClient();
  const [grantOpen, setGrantOpen] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["agent-kbs", agentId],
    queryFn: () => apiFetch<AgentKbRow[]>(`/agents/${agentId}/knowledge-bases`),
    refetchOnWindowFocus: false,
  });

  const revokeMutation = useMutation({
    mutationFn: (kbId: string) =>
      apiFetch<void>(`/agents/${agentId}/knowledge-bases/${kbId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-kbs", agentId] });
    },
  });

  return (
    <Card data-testid="agent-kbs-section">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">
          <span className="inline-flex items-center gap-2">
            <Library className="h-4 w-4" /> Knowledge Bases
          </span>
        </CardTitle>
        {!isReadOnly && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setGrantOpen(true)}
            data-testid="agent-kb-grant-button"
          >
            <Plus className="mr-1 h-3.5 w-3.5" /> Grant KB
          </Button>
        )}
      </CardHeader>

      <CardContent>
        <StateBlock
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={Boolean(data && data.length === 0)}
          loadingLabel="Cargando KBs…"
          errorTitle="No se pudo cargar las KBs"
          empty={
            <p className="text-muted-foreground text-sm" data-testid="agent-kbs-empty">
              Este agente no tiene KBs propias. Las KBs de proyecto siguen siendo visibles en
              runtime — éstas se añaden cuando el rol necesita documentación agnóstica de stack
              (e.g. principios de diseño REST).
            </p>
          }
        >
          {data && data.length > 0 && (
            <ul className="space-y-2" data-testid="agent-kbs-list">
              {data.map((row) => (
                <li
                  key={row.kb_id}
                  className="flex items-start justify-between gap-3 rounded border p-3"
                  data-testid={`agent-kb-row-${row.kb_id}`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-sm">{row.name}</p>
                    {row.description && (
                      <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                        {row.description}
                      </p>
                    )}
                    <p className="text-muted-foreground mt-1 font-mono text-xs">
                      embedding: {row.embedding_model_id}
                    </p>
                  </div>
                  {!isReadOnly && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => revokeMutation.mutate(row.kb_id)}
                      disabled={revokeMutation.isPending}
                      data-testid={`agent-kb-revoke-${row.kb_id}`}
                      title="Revocar grant"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </StateBlock>
      </CardContent>

      <GrantKbDialog
        agentId={agentId}
        open={grantOpen}
        onOpenChange={setGrantOpen}
        existingKbIds={(data ?? []).map((r) => r.kb_id)}
        onGranted={() => {
          void queryClient.invalidateQueries({ queryKey: ["agent-kbs", agentId] });
          setGrantOpen(false);
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Grant dialog
// ---------------------------------------------------------------------------
function GrantKbDialog({
  agentId,
  open,
  onOpenChange,
  existingKbIds,
  onGranted,
}: {
  agentId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  existingKbIds: string[];
  onGranted: () => void;
}) {
  const errorText = useErrorText();
  const [pickedKb, setPickedKb] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (kbId: string) =>
      apiFetch<unknown>(`/agents/${agentId}/knowledge-bases`, {
        method: "POST",
        body: { kb_id: kbId },
      }),
    onSuccess: () => {
      setPickedKb(null);
      setError(null);
      onGranted();
    },
    onError: (err) => {
      setError(errorText(err));
    },
  });

  const isDuplicate = pickedKb !== null && existingKbIds.includes(pickedKb);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="md">
      <DialogContent data-testid="agent-kb-grant-dialog">
        <DialogHeader>
          <DialogTitle>Asignar KB al agente</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-muted-foreground text-sm">
            La KB se hará visible cuando el agente ejecute en cualquier proyecto (es un grant de
            rol, no de stack).
          </p>
          <KbCombobox value={pickedKb} onChange={(id) => setPickedKb(id)} />
          {isDuplicate && (
            <p className="text-warning-soft-foreground text-xs">
              Esta KB ya está granteada — la operación será idempotente.
            </p>
          )}
          {error && <p className="text-danger-soft-foreground text-sm">{error}</p>}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            onClick={() => pickedKb && mutation.mutate(pickedKb)}
            disabled={pickedKb === null || mutation.isPending}
            data-testid="agent-kb-grant-confirm"
          >
            {mutation.isPending ? "Asignando…" : "Asignar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
