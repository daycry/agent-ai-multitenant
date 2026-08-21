"use client";

import { type FormEvent, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

interface MfaChallengeProps {
  /** Single-use challenge token del paso password/SSO (`mfa_required`). */
  mfaToken: string;
  /** Recibe la LoginResponse definitiva tras verificar el segundo factor. */
  onSuccess: (session: LoginResponse) => void;
}

/**
 * Paso de segundo factor del login (MFA UI, tanda 2026-07-19).
 *
 * El backend (Plan 08) responde al password con `mfa_required` + mfa_token
 * cuando el usuario tiene TOTP confirmado; este formulario canjea ese token
 * más el código de 6 dígitos (o un código de recuperación) en
 * `/auth/mfa/totp/verify` por la sesión real. El challenge caduca en
 * minutos y es de un solo uso: un fallo de verificación mantiene el
 * formulario para reintentar; un challenge caducado devuelve al login.
 */
export function MfaChallenge({ mfaToken, onSuccess }: MfaChallengeProps) {
  // i18n vía diccionario (prod-16 `task_prod16_02`). Este paso quedó fuera de
  // la migración del login del 08-01 porque el test de la pantalla no lo
  // renderiza nunca: sólo aparece con `mfa_required`, o sea con TOTP activado.
  const t = useT("login");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const session = await apiFetch<LoginResponse>("/auth/mfa/totp/verify", {
        method: "POST",
        body: { mfa_token: mfaToken, code: code.trim() },
      });
      onSuccess(session);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(t("mfaErrorInvalidCode"));
      } else if (err instanceof ApiError && (err.status === 400 || err.status === 410)) {
        setError(t("mfaErrorExpired"));
      } else {
        setError(t("errorUnreachable"));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" data-testid="mfa-form">
      <div className="flex items-center gap-2">
        <ShieldCheck className="text-muted-foreground h-5 w-5" aria-hidden="true" />
        <p className="text-muted-foreground text-sm">{t("mfaHelp")}</p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="mfa-code">{t("mfaCodeLabel")}</Label>
        <Input
          id="mfa-code"
          data-testid="mfa-code-input"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          required
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </div>
      {error && (
        <p className="text-destructive text-sm" role="alert" data-testid="mfa-error">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={loading || code.trim().length === 0}>
        {loading && <Spinner className="mr-2 h-4 w-4" />}
        {loading ? t("mfaSubmitting") : t("mfaSubmit")}
      </Button>
    </form>
  );
}
