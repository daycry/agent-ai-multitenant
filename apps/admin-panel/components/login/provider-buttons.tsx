"use client";

/**
 * Branded SSO provider buttons for `/login` (ADR 0047, task_sso_05).
 *
 * Fetches the PUBLIC `GET /auth/sso/providers` list (no auth, no secrets —
 * id / kind / display_name / button_label / login_url) and renders one
 * brand button per ENABLED global provider, alongside the password form.
 *
 * Auth is platform-global: there is NO tenant in the URL. Clicking a
 * button does a full-page navigation to the provider's `login_url` on the
 * api-server, which 307/302-redirects the browser to the IdP. After the
 * IdP round-trip the callback/ACS mints an IDENTITY session; tenant access
 * is then resolved by membership (task_sso_03).
 *
 * The list is fetched directly with `fetch` (NOT `apiFetch`) because the
 * endpoint is public — no token, no `X-Tenant-Id` — and we never want a
 * stale token to change its (public) response. A fetch error degrades
 * silently to "password only": SSO is ADDED ALONGSIDE local login, never a
 * gate in front of it.
 */

import { useEffect, useState } from "react";

import { Spinner } from "@/components/ui/spinner";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

import { brandSpec, resolveBrand } from "./provider-brand";

/** Mirror of `api_server.schemas.sso.PublicProviderResponse`. No secrets. */
interface PublicProvider {
  id: string;
  kind: string;
  display_name: string | null;
  button_label: string | null;
  login_url: string;
}

export function ProviderButtons() {
  const [providers, setProviders] = useState<PublicProvider[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Public endpoint: no Authorization / X-Tenant-Id headers.
    fetch(apiUrl("/auth/sso/providers"), { headers: { Accept: "application/json" } })
      .then((res) => (res.ok ? (res.json() as Promise<PublicProvider[]>) : []))
      .then((data) => {
        if (!cancelled) setProviders(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        // SSO is optional/additive — a failure just means "password only".
        if (!cancelled) setProviders([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-2" data-testid="login-providers-loading">
        <Spinner className="text-muted-foreground h-4 w-4" />
      </div>
    );
  }

  if (providers.length === 0) {
    // No enabled providers — render nothing; the password form stands
    // alone (no orphan "or with email" divider).
    return null;
  }

  return (
    <div className="space-y-5">
      {/* Divider first, then the buttons: the password form sits ABOVE, so
          these providers are the alternative below it. Only renders when at
          least one provider is enabled. */}
      <div className="flex items-center gap-3" aria-hidden="true" data-testid="login-divider">
        <span className="bg-border h-px flex-1" />
        <span className="text-muted-foreground text-xs uppercase tracking-wide">
          o continúa con
        </span>
        <span className="bg-border h-px flex-1" />
      </div>

      <div className="space-y-2" data-testid="login-providers">
        {providers.map((provider) => (
          <ProviderButton key={provider.id} provider={provider} />
        ))}
      </div>
    </div>
  );
}

function ProviderButton({ provider }: { provider: PublicProvider }) {
  const brand = resolveBrand(provider.kind, provider.button_label, provider.display_name);
  const spec = brandSpec(brand);
  const label = provider.button_label?.trim() || spec.defaultLabel;

  function start() {
    // Full-page navigation to the api-server login route; it redirects to
    // the IdP. The login_url is server-relative.
    window.location.href = apiUrl(provider.login_url);
  }

  return (
    <button
      type="button"
      onClick={start}
      data-testid={`login-provider-${provider.id}`}
      data-brand={brand}
      aria-label={label}
      className={cn(
        "ring-offset-background inline-flex h-10 w-full items-center justify-center gap-2.5 rounded-md px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
        spec.className,
      )}
    >
      <spec.Logo className="h-[1.15rem] w-[1.15rem] shrink-0" />
      <span className="truncate">{label}</span>
    </button>
  );
}
