"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import {
  INSTALL_STEPS,
  streamInstall,
  type InstallStepId,
  type InstallStepStatus,
  type LogLine,
  type ProgressEvent,
} from "./install";

/** Overall install run phase. */
export type InstallPhase = "idle" | "running" | "done" | "failed" | "aborted";

export interface InstallController {
  phase: InstallPhase;
  /** Per-step status, keyed by step id. */
  stepStatus: Readonly<Record<InstallStepId, InstallStepStatus>>;
  /** Accumulated log lines (in arrival order). */
  log: readonly LogLine[];
  /** Coarse 0-100 progress. */
  percent: number;
  /** The error message of the failed step, if any. */
  error: string | null;
  /** Start (or retry) the install run with the given secret-free config echo. */
  start: (config: Record<string, unknown>) => void;
  /** Abort an in-flight run. */
  abort: () => void;
}

function initialStatus(): Record<InstallStepId, InstallStepStatus> {
  return INSTALL_STEPS.reduce(
    (acc, step) => {
      acc[step.id] = "pending";
      return acc;
    },
    {} as Record<InstallStepId, InstallStepStatus>,
  );
}

const STEP_IDS: readonly InstallStepId[] = INSTALL_STEPS.map((s) => s.id);

function isStepId(stage: string): stage is InstallStepId {
  return (STEP_IDS as readonly string[]).includes(stage);
}

/**
 * Client-side install run state machine. Consumes the backend SSE stream and
 * derives per-step status + a live log + overall phase. No provisioning happens
 * here — the backend orchestrator (behind its executor seam) does the work; this
 * hook only reflects the stream. Retry simply restarts the stream (the backend
 * resumes from the first non-OK step). Abort cancels the in-flight request.
 */
export function useInstall(): InstallController {
  const [phase, setPhase] = useState<InstallPhase>("idle");
  const [stepStatus, setStepStatus] =
    useState<Record<InstallStepId, InstallStepStatus>>(initialStatus);
  const [log, setLog] = useState<LogLine[]>([]);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const handleEvent = useCallback((event: ProgressEvent) => {
    setPercent(event.percent);
    if (event.failed) {
      setError(event.message);
      setPhase("failed");
      if (isStepId(event.stage)) {
        setStepStatus((prev) => ({ ...prev, [event.stage]: "failed" }));
      }
      setLog((prev) => [...prev, { stage: event.stage, message: event.message, failed: true }]);
      return;
    }
    if (event.done) {
      setPhase("done");
      setPercent(100);
      return;
    }
    if (isStepId(event.stage)) {
      const stage = event.stage;
      setStepStatus((prev) => {
        const next = { ...prev };
        // The first event for a step is its "running" marker; the trailing
        // "completado." line flips it to ok. We mark running on first sight and
        // ok when the completion message arrives.
        if (event.message.endsWith("completado.")) {
          next[stage] = "ok";
        } else if (next[stage] === "pending") {
          next[stage] = "running";
        }
        return next;
      });
    }
    setLog((prev) => [...prev, { stage: event.stage, message: event.message, failed: false }]);
  }, []);

  const start = useCallback(
    (config: Record<string, unknown>) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Reset transient run state; on retry, already-OK steps will be re-marked
      // ok by the backend's resumed stream.
      setError(null);
      setLog([]);
      setStepStatus(initialStatus());
      setPercent(0);
      setPhase("running");

      void streamInstall(config, handleEvent, controller.signal).catch((err: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Error de instalación desconocido");
        setPhase("failed");
      });
    },
    [handleEvent],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("aborted");
  }, []);

  return useMemo(
    () => ({ phase, stepStatus, log, percent, error, start, abort }),
    [phase, stepStatus, log, percent, error, start, abort],
  );
}
