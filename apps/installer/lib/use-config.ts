"use client";

import { useCallback, useMemo, useState } from "react";

import { emptyConfig, type InstallerConfig } from "./config";

export interface ConfigController {
  config: InstallerConfig;
  /** Replace one top-level section immutably (e.g. `update("system", {...})`). */
  update: <K extends keyof InstallerConfig>(section: K, value: InstallerConfig[K]) => void;
  /** Patch a subset of fields of one section. */
  patch: <K extends keyof InstallerConfig>(
    section: K,
    partial: Partial<InstallerConfig[K]>,
  ) => void;
}

/**
 * Holds the captured config (wizard steps 2-6) in client state. Pure: no host
 * access, no provisioning. Secrets live here only until POSTed to the backend
 * and are never persisted nor logged. Lifted to the wizard shell so every step
 * form reads/writes the same source of truth.
 */
export function useConfig(initial: InstallerConfig = emptyConfig()): ConfigController {
  const [config, setConfig] = useState<InstallerConfig>(initial);

  const update = useCallback(
    <K extends keyof InstallerConfig>(section: K, value: InstallerConfig[K]) => {
      setConfig((prev) => ({ ...prev, [section]: value }));
    },
    [],
  );

  const patch = useCallback(
    <K extends keyof InstallerConfig>(section: K, partial: Partial<InstallerConfig[K]>) => {
      setConfig((prev) => ({ ...prev, [section]: { ...prev[section], ...partial } }));
    },
    [],
  );

  return useMemo(() => ({ config, update, patch }), [config, update, patch]);
}
