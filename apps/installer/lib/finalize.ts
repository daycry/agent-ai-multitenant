/**
 * Client-side types + fetch for the finalize step 9 (Plan 15 task_15_06).
 * Mirrors the backend's `/api/finalize/*` routes
 * (`apps/installer/backend/src/installer_backend/main.py`).
 *
 * Step 9 reveals the generated credentials + Vault unseal keys EXACTLY ONCE
 * (Decisiones Clave: no recovery — the operator must save them), then the
 * installer self-destructs. The reveal is the only surface that ever carries
 * the secret values; the backend serves it once and then a second fetch is
 * `410 Gone`. No host access happens in the browser. The e2e spec mocks these
 * routes.
 */

import { INSTALLER_API_BASE } from "./prereqs";

/** One labelled credential line in the one-time reveal. */
export interface CredentialField {
  readonly key: string;
  readonly label_es: string;
  readonly label_en: string;
  /** The secret value — shown once, never re-fetchable. */
  readonly secret: string;
}

/** The one-time reveal payload (mirrors the backend RevealResponse). */
export interface RevealPayload {
  readonly credentials: readonly CredentialField[];
  readonly unseal_keys: readonly string[];
  readonly warning_es: string;
  readonly warning_en: string;
}

/** Non-secret finalize status (mirrors the backend FinalizeStatusResponse). */
export interface FinalizeStatus {
  readonly installed: boolean;
  readonly can_reveal: boolean;
  readonly revealed: boolean;
}

/** Fetch the non-secret finalize gate status. */
export async function fetchFinalizeStatus(signal?: AbortSignal): Promise<FinalizeStatus> {
  const resp = await fetch(`${INSTALLER_API_BASE}/api/finalize/status`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!resp.ok) {
    throw new Error(`finalize status failed: HTTP ${resp.status}`);
  }
  return (await resp.json()) as FinalizeStatus;
}

/** Result of attempting the one-time reveal. */
export type RevealResult =
  | { readonly kind: "ok"; readonly payload: RevealPayload }
  /** The install never completed (409) — nothing to reveal. */
  | { readonly kind: "incomplete" }
  /** The one-time payload was already served (410) — gone, no recovery. */
  | { readonly kind: "gone" };

/**
 * Reveal the credentials + unseal keys exactly once.
 *
 * The backend returns 200 with the payload on the first call, 410 once it has
 * already been served (the payload is gone), and 409 if the install never
 * completed. We map those to a discriminated result so the UI can render the
 * right state without leaking a secret on the non-ok paths.
 */
export async function revealCredentials(signal?: AbortSignal): Promise<RevealResult> {
  const resp = await fetch(`${INSTALLER_API_BASE}/api/finalize/reveal`, {
    method: "POST",
    headers: { Accept: "application/json" },
    signal,
  });
  if (resp.ok) {
    return { kind: "ok", payload: (await resp.json()) as RevealPayload };
  }
  if (resp.status === 410) {
    return { kind: "gone" };
  }
  if (resp.status === 409) {
    return { kind: "incomplete" };
  }
  throw new Error(`finalize reveal failed: HTTP ${resp.status}`);
}
