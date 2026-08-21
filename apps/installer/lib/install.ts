/**
 * Client-side types + stream consumer for the install step 8 (Plan 15
 * task_15_05). Mirrors the backend's install orchestration
 * (`apps/installer/backend/src/installer_backend/install.py`) and its SSE
 * stream route `POST /api/install/stream`.
 *
 * The backend runs the real provisioning behind an injectable executor seam;
 * the wizard UI only renders the per-step status and the live log. No host
 * access happens in the browser. The e2e spec mocks the stream route.
 */

import { INSTALLER_API_BASE } from "./prereqs";

/** The ordered install pipeline steps (mirrors the backend enum). */
export type InstallStepId =
  "generate_config" | "pull_images" | "start_stack" | "bootstrap_vault" | "seed_tenant";

/** Per-step lifecycle status (mirrors the backend StepStatus). */
export type InstallStepStatus = "pending" | "running" | "ok" | "failed";

export interface InstallStepInfo {
  readonly id: InstallStepId;
  readonly index: number;
  readonly titleEs: string;
  readonly titleEn: string;
}

/**
 * Canonical pipeline definition, mirrored from the backend so the progress view
 * can render the step list before the stream starts. The backend's
 * `/api/install/steps` route is the authoritative source; this is the fallback
 * the UI seeds from.
 */
export const INSTALL_STEPS: readonly InstallStepInfo[] = [
  {
    id: "generate_config",
    index: 0,
    titleEs: "Generar configuración",
    titleEn: "Generate configuration",
  },
  { id: "pull_images", index: 1, titleEs: "Descargar imágenes", titleEn: "Pull images" },
  { id: "start_stack", index: 2, titleEs: "Arrancar el stack", titleEn: "Start the stack" },
  { id: "bootstrap_vault", index: 3, titleEs: "Inicializar Vault", titleEn: "Bootstrap Vault" },
  { id: "seed_tenant", index: 4, titleEs: "Crear tenant inicial", titleEn: "Seed initial tenant" },
] as const;

/** One progress event streamed from the backend (mirrors ProgressEvent). */
export interface ProgressEvent {
  /** The pipeline step this event belongs to, or "done" for the terminal one. */
  readonly stage: string;
  /** Human log line — NEVER carries a secret (asserted backend-side). */
  readonly message: string;
  /** Coarse 0-100 progress estimate. */
  readonly percent: number;
  /** True on the terminal success event. */
  readonly done: boolean;
  /** True on a step-failure event; the pipeline halted. */
  readonly failed: boolean;
}

/** A single log line accumulated by the progress view. */
export interface LogLine {
  readonly stage: string;
  readonly message: string;
  readonly failed: boolean;
}

/**
 * Consume the install SSE stream, invoking `onEvent` for each parsed event.
 *
 * Uses `fetch` + a streaming reader (not `EventSource`) because the stream is
 * started by a POST that carries the captured (secret-free) config. The body
 * is decoded incrementally and split on the SSE frame delimiter; partial
 * frames are buffered across chunks. Aborts cleanly via the AbortSignal.
 */
export async function streamInstall(
  config: Record<string, unknown>,
  onEvent: (event: ProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${INSTALLER_API_BASE}/api/install/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ config }),
    signal,
  });
  if (!resp.ok || resp.body === null) {
    throw new Error(`install stream failed: HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushFrames = (): void => {
    let delimiter = buffer.indexOf("\n\n");
    while (delimiter !== -1) {
      const frame = buffer.slice(0, delimiter);
      buffer = buffer.slice(delimiter + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data:")) {
          const payload = line.slice("data:".length).trim();
          if (payload) {
            onEvent(JSON.parse(payload) as ProgressEvent);
          }
        }
      }
      delimiter = buffer.indexOf("\n\n");
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    flushFrames();
  }
  // Flush any trailing frame without a closing delimiter.
  buffer += "\n\n";
  flushFrames();
}
