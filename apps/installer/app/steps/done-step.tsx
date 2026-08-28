"use client";

import { AlertTriangle, KeyRound, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { revealCredentials, type RevealPayload } from "@/lib/finalize";

import { useInstallerMode } from "../simulation-notice";

/** UI phase of the one-time reveal. */
type RevealPhase = "loading" | "revealed" | "gone" | "incomplete" | "error";

/**
 * Step 9 — Listo: credenciales mostradas UNA vez + autodestrucción (task_15_06).
 *
 * On mount it asks the backend for the one-time reveal. The backend serves the
 * generated credentials + Vault unseal keys exactly once and then self-destructs
 * the installer; a second fetch is `410 Gone`. So this component reveals the
 * secrets a single time, warns the operator to save them now (no recovery), and
 * never re-requests them. The e2e spec mocks `/api/finalize/reveal`.
 *
 * ## La pantalla que mentía
 *
 * Hasta el 2026-08-28 esta cabecera decía «Instalación completada · La
 * plataforma está instalada» **siempre**, incluso —y sobre todo— cuando el
 * backend estaba simulado, que es el caso por defecto. Debajo servía un usuario,
 * una contraseña, un root token de Vault y cinco unseal keys fabricadas con
 * `secrets.token_urlsafe`, bajo el aviso de que se muestran una sola vez y no
 * hay forma de recuperarlas. El operador las apuntaba. No abrían nada.
 *
 * Ahora la cabecera la decide el modo (`/api/mode`), y en simulación no existe
 * ninguna frase que diga que hay algo instalado. El backend además marca su
 * propia respuesta (`simulated` en `/api/finalize/reveal`) para los clientes que
 * no son este navegador — un `curl`, un script — porque el aviso tiene que
 * viajar con las credenciales, no sólo al lado.
 */
export function DoneStep() {
  const mode = useInstallerMode();
  const [phase, setPhase] = useState<RevealPhase>("loading");
  const [payload, setPayload] = useState<RevealPayload | null>(null);
  const requestedRef = useRef(false);

  // Reveal exactly once on mount. We guard with a ref so React StrictMode's
  // double-invoke (dev) doesn't fire two reveals — the backend would 410 the
  // second anyway, but we must not race the operator's only view of the secrets.
  useEffect(() => {
    if (requestedRef.current) {
      return;
    }
    requestedRef.current = true;
    const controller = new AbortController();
    void revealCredentials(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) {
          return;
        }
        if (result.kind === "ok") {
          setPayload(result.payload);
          setPhase("revealed");
        } else if (result.kind === "gone") {
          setPhase("gone");
        } else {
          setPhase("incomplete");
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        // Surface a generic error — never echo a secret here.
        void err;
        setPhase("error");
      });
    return () => controller.abort();
  }, []);

  return (
    <section data-testid="step-done" className="flex flex-col gap-6">
      {mode.simulated ? (
        <header className="flex flex-col gap-1">
          <h2
            data-testid="done-title"
            data-simulated="true"
            className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-red-600"
          >
            <ShieldAlert className="h-6 w-6 text-red-600" />
            Simulación terminada — no se ha instalado nada
          </h2>
          <p className="max-w-prose text-sm text-red-700 dark:text-red-300">
            No hay ninguna plataforma detrás de esta pantalla: no se ha arrancado ningún stack, no
            se ha inicializado Vault y no existe ningún usuario administrador. Las credenciales de
            abajo son valores desechables recién inventados y <strong>no abren nada</strong>: no las
            guardes.
          </p>
          <p className="max-w-prose text-sm text-red-700/80 dark:text-red-300/80">
            Para instalar de verdad: <code className="font-mono text-xs">{mode.real_path}</code>
          </p>
        </header>
      ) : (
        <header className="flex flex-col gap-1">
          <h2
            data-testid="done-title"
            data-simulated="false"
            className="flex items-center gap-2 text-2xl font-semibold tracking-tight"
          >
            <ShieldCheck className="h-6 w-6 text-emerald-500" />
            Instalación completada
          </h2>
          <p className="text-muted-foreground max-w-prose text-sm">
            La plataforma está instalada. Este instalador se autodestruye tras mostrarte estas
            credenciales.
          </p>
        </header>
      )}

      {phase === "loading" && (
        <p data-testid="reveal-loading" className="text-muted-foreground flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" />
          Recuperando credenciales…
        </p>
      )}

      {phase === "revealed" && payload !== null && <Revealed payload={payload} />}

      {phase === "gone" && (
        <p
          data-testid="reveal-gone"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600"
        >
          Las credenciales ya se mostraron una sola vez y no pueden recuperarse. Si no las
          guardaste, consulta el runbook de rotación de credenciales.
        </p>
      )}

      {phase === "incomplete" && (
        <p
          data-testid="reveal-incomplete"
          className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500"
        >
          La instalación no se completó, así que no hay credenciales que mostrar.
        </p>
      )}

      {phase === "error" && (
        <p
          data-testid="reveal-error"
          className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500"
        >
          No se pudieron recuperar las credenciales. Revisa los logs del instalador.
        </p>
      )}
    </section>
  );
}

function Revealed({ payload }: { payload: RevealPayload }) {
  return (
    <div data-testid="reveal-credentials" className="flex flex-col gap-5">
      <div
        data-testid="reveal-warning"
        className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{payload.warning_es}</span>
      </div>

      <dl className="flex flex-col gap-3">
        {payload.credentials.map((field) => (
          <div
            key={field.key}
            data-testid={`credential-${field.key}`}
            className="border-border flex flex-col gap-1 rounded-md border px-4 py-3"
          >
            <dt className="text-muted-foreground text-xs uppercase tracking-wide">
              {field.label_es}
            </dt>
            <dd
              data-testid={`credential-value-${field.key}`}
              className="break-all font-mono text-sm"
            >
              {field.secret}
            </dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-col gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <KeyRound className="h-4 w-4 text-amber-500" />
          Vault unseal keys
        </h3>
        <ol data-testid="unseal-keys" className="flex flex-col gap-2">
          {payload.unseal_keys.map((key, idx) => (
            <li
              key={idx}
              data-testid="unseal-key"
              className="border-border break-all rounded-md border px-4 py-2 font-mono text-sm"
            >
              {key}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
