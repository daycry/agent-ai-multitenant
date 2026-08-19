"use client";

/**
 * Timeline de versiones de la identidad del córtex (Córtex F3.6, ADR 0074).
 *
 * La identidad evoluciona por reflexión, versionada, y hasta ahora no había forma
 * de ver QUÉ tocó cada versión: la tabla `cortex_identity_history` guarda el
 * `diff`, pero su único lector (`/journal`) lo descarta. Esta tarjeta lo enseña,
 * resumido en lenguaje del owner (`identityDiffSummary`, helper puro y testeado)
 * en vez de volcar el JSONB.
 *
 * ENDPOINT EN CONSTRUCCIÓN: `GET /owner/cortex/identity/history?limit=` lo está
 * añadiendo el carril de backend. Mientras no exista, un 404 se muestra como
 * "todavía no disponible" — NO como un error del owner: acusar al usuario de un
 * fallo que no es suyo es peor que un hueco honesto.
 *
 * Honestidad de producto: la identidad es un modelo computacional que evoluciona,
 * no consciencia. El copy honesto lo pone la página que monta esta tarjeta.
 */

import { useQuery } from "@tanstack/react-query";

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  getCortexIdentityHistory,
  identityDiffSummary,
  identityVersionLabel,
  type CortexIdentityVersion,
} from "@/lib/cortex-identity";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

const HISTORY_LIMIT = 20;

/**
 * Quién movió la identidad, en lenguaje del owner.
 *
 * El mapa ya no guarda TEXTO: guarda la clave de diccionario, porque el copy en
 * castellano fijo dejaba «reflexión» y «tú» dentro del timeline con el panel en
 * inglés. Un valor desconocido se pinta crudo a propósito — es vocabulario del
 * backend y verlo tal cual dice más que esconderlo.
 */
const UPDATED_BY_KEYS = {
  reflection: "byReflection",
  owner_override: "byOwner",
  onboarding: "byOnboarding",
} as const;

export function IdentityTimeline() {
  const lang = useLangOptional();
  const t = useT("cortexIdentity");
  const query = useQuery<CortexIdentityVersion[], ApiError>({
    queryKey: ["cortex", "identity", "history"],
    queryFn: () => getCortexIdentityHistory(HISTORY_LIMIT),
    refetchOnWindowFocus: false,
    retry: false,
  });

  // 404 ⇒ el endpoint aún no está desplegado. Es un estado distinto del error.
  const notDeployed = query.error instanceof ApiError && query.error.status === 404;
  const versions = query.data ?? [];

  return (
    <Card data-testid="cortex-identity-timeline">
      <CardContent className="flex flex-col gap-3 pt-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{t("timelineTitle")}</h2>
          <span className="text-muted-foreground text-xs">{t("timelineSubtitle")}</span>
        </div>

        {query.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {t("timelineLoading")}
          </p>
        ) : notDeployed ? (
          <p
            className="text-muted-foreground text-sm"
            data-testid="cortex-identity-timeline-pending"
          >
            {t("timelinePendingLead")} <code>GET /owner/cortex/identity/history</code>
            {t("timelinePendingTail")}
          </p>
        ) : query.isError ? (
          <p className="text-destructive text-sm" data-testid="cortex-identity-timeline-error">
            {t("timelineError")}
          </p>
        ) : versions.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="cortex-identity-timeline-empty">
            {t("timelineEmpty")}
          </p>
        ) : (
          <ol className="flex flex-col gap-3" data-testid="cortex-identity-timeline-list">
            {versions.map((v) => (
              <li key={v.version} className="border-l-2 pl-3">
                <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-foreground rounded-full border px-2 py-0.5 font-medium">
                    {identityVersionLabel(v.version, lang)}
                  </span>
                  <span className="tabular-nums">{formatWhen(v.created_at)}</span>
                  {v.updated_by ? (
                    <span>
                      ·{" "}
                      {v.updated_by in UPDATED_BY_KEYS
                        ? t(UPDATED_BY_KEYS[v.updated_by as keyof typeof UPDATED_BY_KEYS])
                        : v.updated_by}
                    </span>
                  ) : null}
                  {v.reason ? <span className="italic">({v.reason})</span> : null}
                </div>
                <p className="mt-1 text-sm">{identityDiffSummary(v.diff ?? {}, lang)}</p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}
