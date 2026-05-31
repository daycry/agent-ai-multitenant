"use client";

import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import {
  fetchPrereqs,
  statusLabelEs,
  type PrereqItem,
  type PrereqResponse,
  type PrereqStatus,
} from "@/lib/prereqs";

/**
 * Step 1 — prerequisite validation (Plan 15 task_15_02).
 *
 * Renders the result of the backend's `/api/prereqs` probe: one tri-state row
 * per check (Docker, Compose v2, RAM, disk, GPU) with a remediation message
 * when something is wrong. A hard failure on a required check closes the gate
 * (`onGateChange(false)`) so the wizard shell can disable "next".
 *
 * The real host probing happens server-side behind an injectable seam; the
 * browser only renders and gates. The e2e spec mocks `/api/prereqs`.
 */
interface PrereqPanelProps {
  /** Called whenever the install gate opens/closes (no required failures). */
  onGateChange?: (canProceed: boolean) => void;
  /**
   * When true the panel renders without its own `step-resources` section/title
   * wrapper, so it can be embedded inside the resources step (task_15_03) which
   * owns that wrapper. Defaults to false (standalone, as task_15_02 shipped it).
   */
  embedded?: boolean;
}

const STATUS_ICON: Record<PrereqStatus, typeof CheckCircle2> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  fail: XCircle,
};

const STATUS_CLASS: Record<PrereqStatus, string> = {
  ok: "text-emerald-500",
  warn: "text-amber-500",
  fail: "text-red-500",
};

export function PrereqPanel({ onGateChange, embedded = false }: PrereqPanelProps) {
  const [data, setData] = useState<PrereqResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchPrereqs(signal);
      setData(resp);
      onGateChange?.(resp.can_proceed);
    } catch (err) {
      if (signal?.aborted) {
        return;
      }
      setError(err instanceof Error ? err.message : "Error desconocido");
      onGateChange?.(false);
    } finally {
      setLoading(false);
    }
    // onGateChange is stable from the parent; intentionally excluded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const body = (
    <div className="flex flex-col gap-4" data-testid="prereq-panel">
      <div className="flex items-center justify-between">
        {embedded ? (
          <h3 className="text-lg font-semibold tracking-tight">Prerequisitos</h3>
        ) : (
          <h2 className="text-2xl font-semibold tracking-tight">Recursos / GPU</h2>
        )}
        <button
          type="button"
          data-testid="prereq-recheck"
          onClick={() => void load()}
          className="text-muted-foreground hover:bg-muted inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors"
        >
          <RefreshCw className="h-4 w-4" />
          Volver a comprobar
        </button>
      </div>
      <p className="text-muted-foreground max-w-prose text-sm">
        Comprobamos que esta máquina cumple los prerequisitos para ejecutar el stack: Docker, Docker
        Compose v2, memoria, espacio en disco y, opcionalmente, una GPU NVIDIA.
      </p>

      {loading && (
        <p
          data-testid="prereq-loading"
          className="text-muted-foreground flex items-center gap-2 text-sm"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          Comprobando prerequisitos…
        </p>
      )}

      {error && (
        <p
          data-testid="prereq-error"
          className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500"
        >
          No se pudo contactar con el backend del instalador: {error}
        </p>
      )}

      {data && (
        <ul data-testid="prereq-list" className="flex flex-col gap-2">
          {data.results.map((item) => (
            <PrereqRow key={item.key} item={item} />
          ))}
        </ul>
      )}

      {data && !data.can_proceed && (
        <p
          data-testid="prereq-blocked"
          className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500"
        >
          Hay prerequisitos obligatorios sin cumplir. Resuélvelos y vuelve a comprobar antes de
          continuar.
        </p>
      )}
    </div>
  );

  // Standalone (task_15_02): own the `step-resources` section wrapper. Embedded
  // (task_15_03): the resources step owns the wrapper, so render bare.
  if (embedded) {
    return body;
  }
  return (
    <section data-testid="step-resources" className="flex flex-col gap-4">
      {body}
    </section>
  );
}

function PrereqRow({ item }: { item: PrereqItem }) {
  const Icon = STATUS_ICON[item.status];
  return (
    <li
      data-testid={`prereq-item-${item.key}`}
      data-status={item.status}
      className="border-border flex flex-col gap-1 rounded-md border px-4 py-3"
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4 shrink-0", STATUS_CLASS[item.status])} />
        <span className="font-medium">{item.label}</span>
        <span
          className={cn("ml-auto text-xs uppercase tracking-wide", STATUS_CLASS[item.status])}
          data-testid={`prereq-status-${item.key}`}
        >
          {statusLabelEs(item.status)}
        </span>
      </div>
      {item.detail && <p className="text-muted-foreground pl-6 text-sm">{item.detail}</p>}
      {item.remediation && (
        <p
          data-testid={`prereq-remediation-${item.key}`}
          className="text-muted-foreground pl-6 text-sm"
        >
          {item.remediation}
        </p>
      )}
    </li>
  );
}
