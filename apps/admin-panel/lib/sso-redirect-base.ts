/**
 * Detect whether an SSO callback/ACS URL is still built from the DEFAULT
 * placeholder `sso_redirect_base_url` (ADR 0047, task_sso_05).
 *
 * Auth providers are platform-global; the callback/ACS/SP-entity URLs the
 * operator registers at the IdP are derived from `sso_redirect_base_url`,
 * whose backend default is the placeholder `http://localhost:8000` (it
 * does NOT even match the dev api-server on :8001 — see ADR 0047 §6). The
 * config modal shows the URL informationally and, when it still carries
 * the placeholder, warns the operator to set the real public base URL
 * before wiring up the IdP.
 *
 * We detect it purely from the URL we already display (the backend does
 * not expose a separate "is default" flag), so this stays a frontend-only
 * concern. Matching is host-based to avoid false positives from a path
 * that merely contains the substring.
 */

/** The backend bootstrap default base URL (api_server.config). System Admins
 *  override it live from the SSO page (platform setting `app.public_base_url`),
 *  so this only matches when no override is set. Dev api-server is on :8001. */
export const SSO_REDIRECT_BASE_DEFAULT = "http://localhost:8001";

/**
 * True when `url` (a callback/ACS/SP-entity URL) is built from the default
 * placeholder base. Returns false for any non-URL / empty value so we
 * never warn on a value we cannot parse.
 */
export function isDefaultRedirectBase(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    const base = new URL(SSO_REDIRECT_BASE_DEFAULT);
    return u.protocol === base.protocol && u.host === base.host;
  } catch {
    return false;
  }
}

/**
 * Extract the scheme://host[:port] base from a callback/ACS/SP-entity URL
 * for display ("the configured base URL"). Falls back to the raw value if
 * it cannot be parsed.
 */
export function redirectBaseFromUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.origin;
  } catch {
    return url;
  }
}
