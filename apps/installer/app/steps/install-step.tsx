"use client";

import { CheckCircle2, CircleDashed, Loader2, RotateCcw, XCircle } from "lucide-react";
import { useEffect, useRef } from "react";

import { toWireConfig, type InstallerConfig } from "@/lib/config";
import { INSTALL_STEPS, type InstallStepStatus } from "@/lib/install";
import { useInstall, type InstallPhase } from "@/lib/use-install";
import { cn } from "@/lib/utils";

import { useInstallerMode } from "../simulation-notice";

/**
 * Los campos que NO pueden salir del navegador por este stream.
 *
 * `toWireConfig` los incluye porque el MISMO objeto se postea a
 * `/api/config/validate`, que sí los necesita para responder los `*_set`. Aquí
 * se quitan uno por uno antes de enviar: el backend los rechaza con un `400`
 * desde el 2026-08-28, así que dejarlos sería además un error de red.
 *
 * Espejo de los `SecretStr` de `InstallerConfig`
 * (`installer_backend.main.secret_field_paths`, que los DERIVA del modelo). Si
 * alguien añade un proveedor con credencial y no lo añade aquí, el backend lo
 * dirá con un 400 que nombra el campo — que es exactamente el fallo que avisa
 * dónde está la causa, y por eso la guarda vive allí y no aquí.
 */
const SECRET_WIRE_PATHS: readonly string[] = [
  "storage.minio_secret_key",
  "providers.claude_sdk.oauth_token",
  "providers.copilot.oauth_token",
  "providers.azure_foundry.api_key",
];

/** Copia del eco de config sin los campos secretos. No muta el original. */
export function stripSecrets(wire: Record<string, unknown>): Record<string, unknown> {
  const copy = structuredClone(wire) as Record<string, unknown>;
  for (const path of SECRET_WIRE_PATHS) {
    const segments = path.split(".");
    const leaf = segments.pop() as string;
    let node: Record<string, unknown> | undefined = copy;
    for (const segment of segments) {
      const next: unknown = node?.[segment];
      node =
        typeof next === "object" && next !== null ? (next as Record<string, unknown>) : undefined;
    }
    if (node !== undefined) {
      delete node[leaf];
    }
  }
  return copy;
}

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
 *
 * ⚠️ Cuando el backend está simulado —hoy, por defecto— esta pantalla NO
 * aprovisiona nada: el progreso está guionizado. Se dice aquí y se dice en
 * pantalla, porque el texto que había («Estamos aprovisionando el stack») es
 * justo el que hacía que alguien se lo creyera.
 */
export function InstallStep({ config, onComplete }: InstallStepProps) {
  const install = useInstall();
  const mode = useInstallerMode();
  const startedRef = useRef(false);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // El eco que viaja va SIN secretos, y ahora de verdad.
  //
  // Hasta el 2026-08-28 este comentario decía «Only build the non-secret wire
  // echo» y era falso: `toWireConfig` metía `storage.minio_secret_key` en claro
  // y, por cada proveedor habilitado, su `oauth_token` o su `api_key`, y eso es
  // lo que `install.start()` publicaba como cuerpo del POST. No había daño
  // observable —el backend no los registraba y el ejecutor falso los ignoraba—,
  // pero la afirmación era falsa en tres sitios a la vez, y quien fuera a
  // decidir el futuro del wizard la leería como premisa.
  const wireConfig = stripSecrets(toWireConfig(config));

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
        <h2 className="text-2xl font-semibold tracking-tight">
          {mode.simulated ? "Instalación (simulada)" : "Instalación"}
        </h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          {mode.simulated
            ? "Esta pantalla NO está aprovisionando nada: el progreso y los logs de abajo están " +
              "guionizados por el backend simulado. Sirven para revisar el flujo, no para instalar."
            : "Estamos aprovisionando el stack. No cierres esta ventana hasta que termine. El " +
              "progreso y los logs se actualizan en tiempo real."}
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

      {install.phase === "done" &&
        (mode.simulated ? (
          <p
            data-testid="install-success"
            data-simulated="true"
            className="rounded-md border-2 border-red-600 bg-red-600/10 px-4 py-3 text-sm text-red-600"
          >
            <strong>Simulación completada — no se ha instalado nada.</strong> Ningún stack ha
            arrancado, Vault no se ha inicializado y no existe ningún usuario administrador. Las
            credenciales del paso siguiente son valores desechables que no abren nada.
          </p>
        ) : (
          <p
            data-testid="install-success"
            data-simulated="false"
            className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600"
          >
            Instalación completada. Continúa para ver las credenciales (se muestran una sola vez).
          </p>
        ))}

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
