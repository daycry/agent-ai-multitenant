"use client";

/**
 * Canje de una invitación — ADR 0134 (opción C).
 *
 * El registro público está cerrado: quien llega aquí trae un enlace
 * `/accept-invite?token=<token>` que le pasó un admin desde
 * `/admin/invitations`. La pantalla precarga ese token para que el invitado no
 * tenga que copiar y pegar un secreto largo, pero lo envía en el **cuerpo** de
 * `POST /auth/register`, nunca como query string: un secreto en la URL acaba en
 * los logs de acceso del proxy, en el historial del navegador y en la cabecera
 * `Referer`. Es la misma regla que el repo ya fijó para `X-API-Token`.
 *
 * Sobre los mensajes de error: el backend responde con un 403 **genérico** a
 * todos los motivos de rechazo (token inventado, caducado, revocado, ya
 * canjeado, o emitido para otro email). Es deliberado — distinguirlos volvería
 * a abrir el oráculo de enumeración de emails que este ADR cerró. Así que la UI
 * no se inventa un motivo que no conoce: dice qué hacer, no qué pasó.
 *
 * El alta NO acuña sesión (`/auth/register` devuelve el usuario, no un token),
 * de modo que al terminar se lleva al invitado al login en vez de dejarlo en un
 * limbo con la sensación de estar dentro.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { CheckCircle2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface RegisteredUser {
  id: string;
  email: string;
}

export default function AcceptInvitePage() {
  const t = useT("acceptInvite");
  const router = useRouter();
  const searchParams = useSearchParams();

  const [token, setToken] = useState(() => searchParams?.get("token") ?? "");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await apiFetch<RegisteredUser>("/auth/register", {
        method: "POST",
        body: {
          email,
          password,
          full_name: fullName.trim() === "" ? null : fullName.trim(),
          invitation_token: token,
        },
      });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(t("errorDuplicate"));
      } else if (err instanceof ApiError && err.status === 403) {
        setError(t("errorRejected"));
      } else {
        setError(t("errorUnreachable"));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-12">
      <div className="flex flex-col items-center gap-3">
        <span
          className="bg-brand-gradient inline-flex h-14 w-14 items-center justify-center rounded-2xl shadow-[0_0_36px_-6px_hsl(var(--gradient-from)/0.65)]"
          aria-hidden="true"
        >
          <Sparkles className="h-7 w-7 text-white" />
        </span>
        <div className="text-center">
          {/* "Agentic Platform" es nombre propio: no va al diccionario. */}
          <h1 className="text-foreground text-xl font-semibold tracking-tight">Agentic Platform</h1>
          <p className="text-muted-foreground text-sm">{t("tagline")}</p>
        </div>
      </div>

      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{done ? t("successTitle") : t("title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {done ? (
            <div className="space-y-5" data-testid="accept-invite-success">
              <p className="text-muted-foreground flex items-start gap-2 text-sm">
                <CheckCircle2 className="text-success mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{t("successBody")}</span>
              </p>
              <Button
                className="w-full"
                onClick={() => router.push("/login")}
                data-testid="accept-invite-go-login"
              >
                {t("goToLogin")}
              </Button>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4" data-testid="accept-invite-form">
              <div className="space-y-1.5">
                <Label htmlFor="invite-email">{t("emailLabel")}</Label>
                <Input
                  id="invite-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  data-testid="accept-invite-email"
                />
                <p className="text-muted-foreground text-xs">{t("emailHelp")}</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="invite-name">{t("nameLabel")}</Label>
                <Input
                  id="invite-name"
                  type="text"
                  autoComplete="name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  data-testid="accept-invite-name"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="invite-password">{t("passwordLabel")}</Label>
                <Input
                  id="invite-password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  data-testid="accept-invite-password"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="invite-token">{t("tokenLabel")}</Label>
                <Input
                  id="invite-token"
                  type="text"
                  required
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  data-testid="accept-invite-token"
                />
              </div>
              {error && (
                <p
                  className="text-destructive text-sm"
                  role="alert"
                  data-testid="accept-invite-error"
                >
                  {error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={loading}>
                {loading && <Spinner className="mr-2 h-4 w-4" />}
                {loading ? t("submitting") : t("submit")}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
