"use client";

import { CheckCircle2, CircleDashed, Loader2, RotateCcw, XCircle } from "lucide-react";
import { useEffect, useRef } from "react";

import { toWireConfig, type InstallerConfig } from "@/lib/config";
import { INSTALL_STEPS, type InstallStepStatus } from "@/lib/install";
import { useInstall, type InstallPhase } from "@/lib/use-install";
import { cn } from "@/lib/utils";

interface InstallStepProps {
  /** The captured config (steps 2-6). Only its non-secret echo is streamed. */
  config: InstallerConfig;
  /** Called when the install reaches its terminal success state. */
  onComplete?: () => void;
}

const STATUS_ICON: Record<InstallStepStatus, typeof CheckCircle2> = {
  pending: CircleDashed,
  running: Loader2,
  ok: CheckCircle2,
  failed: XCircle,
};

const STATUS_CLASS: Record<InstallStepStatus, string> = {
  pending: "text-muted-foreground",
  running: "text-sky-500",
  ok: "text-emerald-500",
  failed: "text-red-500",
};

/**
 * Step 8 — Instalación con progreso + logs en tiempo real (Plan 15 task_15_05).
 *
 * Drives the backend install orchestration over SSE and renders a live view:
 * one row per pipeline step with its status (pending/running/ok/failed) and a
 * scrolling log. A failure halts the run, surfaces the error and offers retry.
 * No host access happens in the browser — the backend orchestrator (behind its
 * injectable executor seam) does the work; this component only reflects the
 * stream. The e2e spec mocks `/api/install/stream`.
 */
export function InstallStep({ config, onComplete }: InstallStepProps) {
  const install = useInstall();
  const startedRef = useRef(false);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // Only build the non-secret wire echo for the stream. Secrets reach Vault via
  // Phase B's bootstrap, never over this progress stream.
  const wireConfig = toWireConfig(config);

  // Auto-start the install once when the step mounts.
  useEffect(() => {
    if (!startedRef.current) {
      startedRef.current = true;
      install.start(wireConfig);
    }
    // install.start is stable; wireConfig is captured once on first mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Notify the shell when the run completes so it can enable "next".
  useEffect(() => {
    if (install.phase === "done") {
      onComplete?.();
    }
    // onComplete is stable from the parent; intentionally excluded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [install.phase]);

  // Keep the log scrolled to the latest line.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [install.log]);

  const retry = (): void => {
    install.start(wireConfig);
  };

  return (
    <section data-testid="step-install" className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Instalación</h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          Estamos aprovisionando el stack. No cierres esta ventana hasta que termine. El progreso y
          los logs se actualizan en tiempo real.
        </p>
      </header>

      {/* ----- Progress bar ----- */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm">
          <span data-testid="install-phase" data-phase={install.phase} className="font-medium">
            {phaseLabelEs(install.phase)}
          </span>
          <span data-testid="install-percent" className="text-muted-foreground font-mono text-xs">
            {install.percent}%
          </span>
        </div>
        <div className="bg-muted h-2 w-full overflow-hidden rounded-full">
          <div
            data-testid="install-progress-bar"
            className={cn(
              "h-full rounded-full transition-all",
              install.phase === "failed" ? "bg-red-500" : "bg-primary",
            )}
            style={{ width: `${install.percent}%` }}
          />
        </div>
      </div>

      {/* ----- Per-step status ----- */}
      <ol data-testid="install-steps" className="flex flex-col gap-2">
        {INSTALL_STEPS.map((step) => {
          const status = install.stepStatus[step.id];
          const Icon = STATUS_ICON[status];
          return (
            <li
              key={step.id}
              data-testid={`install-step-${step.id}`}
              data-status={status}
              className="border-border flex items-center gap-3 rounded-md border px-4 py-2.5"
            >
              <Icon
                className={cn(
                  "h-4 w-4 shrink-0",
                  STATUS_CLASS[status],
                  status === "running" && "animate-spin",
                )}
              />
              <span className="font-medium">{step.titleEs}</span>
              <span
                data-testid={`install-step-status-${step.id}`}
                className={cn("ml-auto text-xs uppercase tracking-wide", STATUS_CLASS[status])}
              >
                {stepStatusLabelEs(status)}
              </span>
            </li>
          );
        })}
      </ol>

      {/* ----- Failure + retry ----- */}
      {install.phase === "failed" && (
        <div
          data-testid="install-error"
          className="flex flex-col gap-3 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3"
        >
          <p className="text-sm text-red-500">
            La instalación falló: {install.error ?? "error desconocido"}
          </p>
          <button
            type="button"
            data-testid="install-retry"
            onClick={retry}
            className="text-primary inline-flex w-fit items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-muted"
          >
            <RotateCcw className="h-4 w-4" />
            Reintentar
          </button>
        </div>
      )}

      {install.phase === "done" && (
        <p
          data-testid="install-success"
          className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600"
        >
          Instalación completada. Continúa para ver las credenciales (se muestran una sola vez).
        </p>
      )}

      {/* ----- Live log ----- */}
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold tracking-tight">Logs</h3>
        <div
          data-testid="install-log"
          className="bg-muted/40 max-h-64 overflow-y-auto rounded-md border border-border p-3 font-mono text-xs"
        >
          {install.log.length === 0 ? (
            <p className="text-muted-foreground">Esperando eventos…</p>
          ) : (
            install.log.map((line, idx) => (
              <p
                key={idx}
                data-testid="install-log-line"
                data-stage={line.stage}
                className={cn(line.failed ? "text-red-500" : "text-foreground/80")}
              >
                <span className="text-muted-foreground">[{line.stage}]</span> {line.message}
              </p>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </section>
  );
}

function phaseLabelEs(phase: InstallPhase): string {
  switch (phase) {
    case "idle":
      return "Preparando…";
    case "running":
      return "Instalando…";
    case "done":
      return "Completado";
    case "failed":
      return "Fallida";
    case "aborted":
      return "Cancelada";
  }
}

function stepStatusLabelEs(status: InstallStepStatus): string {
  switch (status) {
    case "pending":
      return "Pendiente";
    case "running":
      return "En curso";
    case "ok":
      return "Correcto";
    case "failed":
      return "Error";
  }
}
