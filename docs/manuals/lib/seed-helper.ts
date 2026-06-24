/** Lee el id del proyecto demo sembrado por lib/seed-demo-data.mjs, para que los
 *  manuales naveguen sub-páginas reales del proyecto (planes, tareas, KB…). */
import fs from "fs";
import path from "path";

/** Lee y parsea un JSON de assets/, tolerando el BOM UTF-8 que añade PowerShell. */
function readAsset<T>(file: string): T | null {
  try {
    const p = path.resolve(__dirname, "..", "assets", file);
    const raw = fs.readFileSync(p, "utf-8").replace(/^﻿/, "");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function seed(): { phpProjectId?: string; phpPlanId?: string | null } {
  return readAsset<{ phpProjectId?: string; phpPlanId?: string | null }>("seed.json") || {};
}

export function seededPhpProjectId(): string {
  // 1) env inyectado por el runner; 2) assets/seed.json (lo escribe el seed).
  if (process.env.MANUALS_PHP_PROJECT_ID) return process.env.MANUALS_PHP_PROJECT_ID;
  return String(seed().phpProjectId || "");
}

export function seededPhpPlanId(): string {
  if (process.env.MANUALS_PHP_PLAN_ID) return process.env.MANUALS_PHP_PLAN_ID;
  return String(seed().phpPlanId || "");
}

export type DockerStack = {
  containers: { name: string; image: string; status: string; ports?: string }[];
  images: { image: string; size: string }[];
  capturedAt?: string;
};

/** Lee el snapshot de `docker ps` capturado por el runner (assets/dockers.json). */
export function dockerStack(): DockerStack {
  const d = readAsset<DockerStack>("dockers.json");
  return { containers: d?.containers || [], images: d?.images || [], capturedAt: d?.capturedAt };
}
