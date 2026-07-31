"use client";

/**
 * Landing page for an SSO login (task_prod09_09, frontend-1).
 *
 * The OIDC callback / SAML ACS used to answer a raw `LoginResponse` JSON, so a
 * user who authenticated at their IdP ended up staring at
 * `{"access_token": "...", ...}` in the browser: the SSO flow was complete on
 * the server and had no last mile. Now the api-server sets the session cookie
 * and 303s here.
 *
 * This page does exactly what the password login does after `/auth/login` —
 * `resolveAndRoute()` (ADR 0047): the session it just received proves IDENTITY
 * only, so the tenant is resolved by membership and the user is routed to the
 * dashboard, the tenant picker or the no-access screen. `replace` (not `push`)
 * so the back button does not walk into a spent callback URL.
 *
 * No new copy: the only visible state is "loading", and a resolution that fails
 * falls back to `/login` rather than parking the user on a dead screen — which
 * is the very failure mode this task exists to remove.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useT } from "@/lib/i18n";
import { resolveAndRoute } from "@/lib/session";

export default function SsoCallbackPage() {
  const router = useRouter();
  const t = useT("common");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await resolveAndRoute();
        if (!cancelled) router.replace(next);
      } catch {
        // A 401 is already handled globally (`lib/api` bounces to /login with
        // the session cleared); any other failure means the session landed but
        // the resolution did not, and spinning forever is the worst answer.
        if (!cancelled) router.replace("/login");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <main
      className="bg-background flex min-h-screen items-center justify-center px-4"
      data-testid="sso-callback"
    >
      <p className="text-muted-foreground text-sm">{t("loading")}</p>
    </main>
  );
}
