"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Sparkles } from "lucide-react";

import { MfaChallenge } from "@/components/login/mfa-challenge";
import { ProviderButtons } from "@/components/login/provider-buttons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { HOME_ROUTE, resolveAndRoute } from "@/lib/session";

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** Respuesta interina del login cuando el usuario tiene TOTP confirmado
 * (backend Plan 08): sin sesión, solo el challenge de un solo uso. */
interface MfaRequiredResponse {
  status: "mfa_required";
  mfa_token: string;
  mfa_methods: string[];
}

function isMfaRequired(data: LoginResponse | MfaRequiredResponse): data is MfaRequiredResponse {
  return (data as MfaRequiredResponse).status === "mfa_required";
}

/**
 * A `?next=` worth honouring: a SERVER-RELATIVE path, nothing else.
 *
 * The parameter is written by `middleware.ts` and by the global 401 handler,
 * but it arrives in the URL, so it is attacker-supplied by definition. A bare
 * "starts with /" check is not enough: `//evil.example` also starts with a
 * slash and the browser reads it as protocol-relative — the classic open
 * redirect, here pointed at a freshly authenticated session.
 */
export function safeNextRoute(raw: string | null): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
  return raw;
}

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // i18n vía diccionario (prod-16 `task_prod16_01`). Antes esta pantalla
  // mezclaba los dos idiomas a mano: "Sign in" junto a "Panel de
  // administración multi-tenant" (hallazgo frontend-9).
  const t = useT("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // MFA (tanda 2026-07-19): challenge interino del backend; con valor, la
  // tarjeta muestra el paso de código en lugar del formulario de password.
  const [mfaToken, setMfaToken] = useState<string | null>(null);

  async function completeSession(_data: LoginResponse) {
    // The login token proves IDENTITY only (no tenant yet). Resolve the
    // user's memberships to decide where to land: enter the tenant
    // directly (single), pick one (multiple) or the no-access screen
    // (none) — ADR 0047 / task_sso_03.
    // ADR 0133: the session arrived as an httpOnly cookie in this very
    // response — there is nothing to store. `data.access_token` is the
    // API-client compatibility leg and the panel must ignore it.
    const resolved = await resolveAndRoute();
    // Come back to where the user was when the session expired — but only if
    // the resolution says they belong in the app at all (a user routed to the
    // tenant picker or the no-access screen must NOT be bounced past it).
    const wanted = safeNextRoute(searchParams.get("next"));
    router.push(wanted && resolved === HOME_ROUTE ? wanted : resolved);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const data = await apiFetch<LoginResponse | MfaRequiredResponse>("/auth/login", {
        method: "POST",
        body: { email, password },
      });
      if (isMfaRequired(data)) {
        setMfaToken(data.mfa_token);
        return;
      }
      await completeSession(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("errorInvalidCredentials"));
      } else if (err instanceof ApiError && err.status === 429) {
        setError(t("errorRateLimited"));
      } else {
        setError(t("errorUnreachable"));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-12">
      <div className="flex flex-col items-center gap-3" data-testid="login-brand">
        <span
          className="bg-brand-gradient inline-flex h-14 w-14 items-center justify-center rounded-2xl shadow-[0_0_36px_-6px_hsl(var(--gradient-from)/0.65)]"
          aria-hidden="true"
        >
          <Sparkles className="h-7 w-7 text-white" />
        </span>
        <div className="text-center">
          {/* "Agentic Platform" es un nombre propio: no va al diccionario. */}
          <h1 className="text-foreground text-xl font-semibold tracking-tight">Agentic Platform</h1>
          <p className="text-muted-foreground text-sm">{t("tagline")}</p>
        </div>
      </div>

      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{mfaToken ? t("mfaTitle") : t("cardTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {mfaToken ? (
            <MfaChallenge mfaToken={mfaToken} onSuccess={completeSession} />
          ) : (
            <>
              <form onSubmit={onSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="email">{t("emailLabel")}</Label>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password">{t("passwordLabel")}</Label>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
                {error && (
                  <p className="text-destructive text-sm" role="alert" data-testid="login-error">
                    {error}
                  </p>
                )}
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading && <Spinner className="mr-2 h-4 w-4" />}
                  {loading ? t("submitting") : t("submit")}
                </Button>
              </form>

              {/* Branded SSO buttons for the enabled GLOBAL providers (ADR
                  0047), shown BELOW the email/password form with an "or continue
                  with" divider. Added ALONGSIDE local login — never a gate in
                  front of it; if no provider is enabled this renders nothing (no
                  divider) and the password form stands alone. */}
              <ProviderButtons />
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
