/**
 * Client-side types + fetch for the prerequisite validation step (Plan 15
 * task_15_02). Mirrors the backend's `/api/prereqs` response
 * (`apps/installer/backend/src/installer_backend/main.py`).
 *
 * The backend runs the real host probes behind an injectable seam; the wizard
 * UI only renders the tri-state result and gates the "next" button on
 * `canProceed`. No host access happens in the browser.
 */

/** Tri-state outcome of a single prerequisite check. */
export type PrereqStatus = "ok" | "warn" | "fail";

export interface PrereqItem {
  readonly key: string;
  readonly label: string;
  readonly status: PrereqStatus;
  /** Derived gate signal: true unless a hard `fail`. */
  readonly ok: boolean;
  readonly detail: string;
  /** Actionable guidance shown when status is `warn`/`fail`; empty when `ok`. */
  readonly remediation: string;
  /** Optional (non-required) checks — e.g. GPU — never block. */
  readonly required: boolean;
}

export interface PrereqResponse {
  readonly results: readonly PrereqItem[];
  readonly all_required_ok: boolean;
  /** True when no required prerequisite is a hard `fail` — the install gate. */
  readonly can_proceed: boolean;
}

/** Base URL of the installer backend; same-origin in the container, env in dev. */
export const INSTALLER_API_BASE = process.env.NEXT_PUBLIC_INSTALLER_API ?? "http://localhost:8080";

/** Fetch the prerequisite check results from the installer backend. */
export async function fetchPrereqs(signal?: AbortSignal): Promise<PrereqResponse> {
  const resp = await fetch(`${INSTALLER_API_BASE}/api/prereqs`, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!resp.ok) {
    throw new Error(`prereq check failed: HTTP ${resp.status}`);
  }
  return (await resp.json()) as PrereqResponse;
}

/** Human-facing label for a status, ES (per docs_language). */
export function statusLabelEs(status: PrereqStatus): string {
  switch (status) {
    case "ok":
      return "Correcto";
    case "warn":
      return "Aviso";
    case "fail":
      return "Error";
  }
}
