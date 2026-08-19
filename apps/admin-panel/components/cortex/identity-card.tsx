"use client";

/**
 * `IdentityCard` — quién dice ser el córtex, en la segunda columna del chat.
 *
 * La casilla F3.6 pedía la tarjeta «en la página de F1 (segunda columna)». Lo
 * entregado en su día fue una ruta hermana, `/admin/cortex/identity`, con el
 * formulario de edición. **Las dos cosas se quedan**, y no por conservadurismo:
 * la ruta la enlazan el NAV (`admin-shell.tsx`), su test de shell y una e2e, y
 * editar identidad pide una pantalla con su formulario. Lo que faltaba es lo que
 * hace esta tarjeta: que el owner VEA con quién habla mientras habla, sin
 * cambiar de pantalla. Editar sigue a un clic (`cortex-identity-card-edit`).
 *
 * Es de solo lectura a propósito. Los rasgos los deriva la reflexión de forma
 * clampeada y versionada (guardrail de auto-modificación, ADR 0074) y el resto
 * se edita en la pantalla hermana; una tarjeta que además guardara duplicaría el
 * formulario y sus validaciones, que es como los dos empiezan a divergir.
 *
 * Honestidad de producto (ADR 0074/0077): la identidad es un MODELO
 * COMPUTACIONAL que evoluciona, no consciencia ni un «yo» real. El aviso se
 * pinta SIEMPRE — también cuando la carga falla —, sale del diccionario y por
 * tanto está en ES **y** EN. Ése era el punto exacto por el que la casilla
 * seguía abierta: el aviso era un `const` en castellano.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Info, Pencil, Sparkles } from "lucide-react";

import { TraitRadar } from "@/components/cortex/trait-radar";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  getCortexIdentity,
  identityVersionLabel,
  needsOnboarding,
  type CortexIdentity,
} from "@/lib/cortex-identity";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { renderPlanDraft } from "@/lib/plan-draft-md";

/** La ruta hermana con el formulario completo (NAV + e2e la enlazan). */
export const CORTEX_IDENTITY_HREF = "/admin/cortex/identity";

export function IdentityCard() {
  const t = useT("cortexIdentity");
  const lang = useLangOptional();
  const query = useQuery<CortexIdentity, ApiError>({
    queryKey: ["cortex", "identity"],
    queryFn: getCortexIdentity,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const identity = query.data ?? null;
  const pendingOnboarding = identity ? needsOnboarding(identity) : false;

  return (
    <Card data-testid="cortex-identity-card">
      <CardContent className="flex flex-col gap-4 pt-5">
        <div className="flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="h-4 w-4 shrink-0" aria-hidden="true" />
            {t("title")}
          </h2>
          <Link
            href={CORTEX_IDENTITY_HREF}
            data-testid="cortex-identity-card-edit"
            className="text-muted-foreground hover:text-foreground inline-flex shrink-0 items-center gap-1 text-xs"
          >
            <Pencil className="h-3 w-3" aria-hidden="true" />
            {t("editLink")}
          </Link>
        </div>

        {/* Aviso honesto: SIEMPRE, antes de cualquier rama de estado. */}
        <p
          className="text-muted-foreground flex items-start gap-2 text-xs"
          data-testid="cortex-identity-card-honesty"
        >
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t("honestyNote")}
        </p>

        {query.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {t("loading")}
          </p>
        ) : query.isError || !identity ? (
          // Un fallo de carga NO es «no hay identidad»: decirle al owner que su
          // córtex no tiene identidad cuando lo que hubo fue un 500 es mentirle.
          <p className="text-destructive text-sm" data-testid="cortex-identity-card-error">
            {t("loadError")}
          </p>
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <p className="truncate text-base font-medium" data-testid="cortex-identity-card-name">
                {identity.name?.trim() || t("unnamed")}
              </p>
              <span className="text-muted-foreground shrink-0 text-xs">
                {identityVersionLabel(identity.version, lang)}
              </span>
            </div>

            {pendingOnboarding ? (
              <div
                className="border-primary/40 bg-primary/5 rounded-lg border px-3 py-2 text-sm"
                data-testid="cortex-identity-card-onboarding"
              >
                <p className="font-medium">{t("onboardingTitle")}</p>
                <p className="text-muted-foreground mt-1 text-xs">{t("onboardingBody")}</p>
              </div>
            ) : null}

            <TraitRadar traits={identity.traits} />

            <ChipList title={t("valuesTitle")} items={identity.core_values} testid="values" />
            <ChipList title={t("goalsTitle")} items={identity.learning_goals} testid="goals" />

            <div className="flex flex-col gap-1">
              <p className="text-muted-foreground text-xs uppercase tracking-wider">
                {t("narrativeTitle")}
              </p>
              <div className="text-sm leading-relaxed" data-testid="cortex-identity-card-narrative">
                {identity.narrative.trim() ? (
                  // Mismo renderer markdown XSS-safe que usan el chat del córtex
                  // y la preview del formulario: la narrativa se escribe en
                  // Markdown y verla con asteriscos crudos sería un fallo visible.
                  renderPlanDraft(identity.narrative)
                ) : (
                  <p className="text-muted-foreground">{t("narrativeEmpty")}</p>
                )}
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Una lista corta (valores, objetivos) como chips; vacía, no se pinta. */
function ChipList({
  title,
  items,
  testid,
}: {
  title: string;
  items: readonly string[];
  testid: string;
}) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-muted-foreground text-xs uppercase tracking-wider">{title}</p>
      <ul className="flex flex-wrap gap-1.5" data-testid={`cortex-identity-card-${testid}`}>
        {items.map((item) => (
          <li key={item} className="bg-muted rounded-full px-2 py-0.5 text-xs">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
