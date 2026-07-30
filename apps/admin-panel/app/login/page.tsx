"use client";

import { useRouter } from "next/navigation";
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
import { setToken } from "@/lib/auth";
import { resolveAndRoute } from "@/lib/session";

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

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // MFA (tanda 2026-07-19): challenge interino del backend; con valor, la
  // tarjeta muestra el paso de código en lugar del formulario de password.
  const [mfaToken, setMfaToken] = useState<string | null>(null);

  async function completeSession(data: LoginResponse) {
    // The login token proves IDENTITY only (no tenant yet). Resolve the
    // user's memberships to decide where to land: enter the tenant
    // directly (single), pick one (multiple) or the no-access screen
    // (none) — ADR 0047 / task_sso_03.
    setToken(data.access_token);
    const next = await resolveAndRoute();
    router.push(next);
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
        setError("Invalid email or password.");
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Please wait and try again.");
      } else {
        setError("Could not reach the server.");
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
          <h1 className="text-foreground text-xl font-semibold tracking-tight">Agentic Platform</h1>
          <p className="text-muted-foreground text-sm">Panel de administración multi-tenant</p>
        </div>
      </div>

      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{mfaToken ? "Verificación en dos pasos" : "Sign in"}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {mfaToken ? (
            <MfaChallenge mfaToken={mfaToken} onSuccess={completeSession} />
          ) : (
            <>
              <form onSubmit={onSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
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
                  <Label htmlFor="password">Password</Label>
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
                  {loading ? "Signing in…" : "Sign in"}
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
