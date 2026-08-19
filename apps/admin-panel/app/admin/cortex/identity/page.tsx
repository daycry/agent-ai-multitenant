"use client";

/**
 * Identidad del córtex — onboarding co-diseñado + edición (Córtex F3, ADR 0074/0077).
 *
 * Vista HERMANA del chat (`app/admin/cortex/page.tsx`) y del Panel de Mente
 * (`app/admin/cortex/mind/page.tsx`): mismo gate (`isSystemOwner`; un no-owner ve
 * `cortex-identity-no-access` y el backend `require_system_owner` es la barrera
 * real, ADR 0074). El System Owner co-diseña la identidad del córtex:
 *
 *   - Edita `name` / `core_values` / `narrative` (con preview Markdown) /
 *     `language` / `learning_goals`.
 *   - Ve (solo-lectura) los rasgos Big-Five y el baseline de ánimo, que DERIVA la
 *     reflexión periódica de forma clampeada y versionada — el owner NO los pisa.
 *   - Si nunca hubo onboarding (`onboarded_at` NULL), un banner prominente invita
 *     a "ponle nombre y valores a tu córtex".
 *   - Puede disparar una pasada de reflexión bajo demanda (best-effort).
 *
 * Honestidad de producto OBLIGATORIA: la identidad es un MODELO COMPUTACIONAL que
 * evoluciona, NO consciencia ni un "yo" real. El copy no insinúa lo contrario, y
 * desde el cierre de la casilla F3.6 sale del diccionario
 * (`cortexIdentity.honestyNote`) en vez de ser un `const` en castellano: lo pide
 * el principio rector 12 (ES+EN), y era el motivo exacto por el que la casilla
 * seguía abierta. La MISMA clave la usa la tarjeta de la segunda columna del
 * chat, para que no haya dos avisos que puedan divergir.
 *
 * Esta ruta NO desaparece con la llegada de esa tarjeta: la enlazan el NAV, su
 * test de shell y una e2e, y aquí vive el formulario. La tarjeta es la vista
 * mientras conversas; esto, el sitio donde se edita.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Info, RefreshCw, Save, Sparkles } from "lucide-react";

import { IdentityTimeline } from "@/components/cortex/identity-timeline";
import { TraitRadar } from "@/components/cortex/trait-radar";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  CORTEX_IDENTITY_LIMITS,
  CORTEX_LANGUAGES,
  getCortexIdentity,
  joinLines,
  needsOnboarding,
  parseLines,
  reflectCortexIdentity,
  updateCortexIdentity,
  type CortexIdentity,
} from "@/lib/cortex-identity";
import { useT } from "@/lib/i18n";
import { useCurrentUser } from "@/lib/use-current-user";

export default function CortexIdentityPage() {
  const { isSystemOwner, isLoading: userLoading } = useCurrentUser();
  const tCommon = useT("common");

  // Mientras no sabemos el rol, nada: nunca parpadear contenido owner-only.
  if (userLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          {tCommon("loading")}
        </p>
      </div>
    );
  }

  // No-owner -> sin formulario. El backend (require_system_owner) es la barrera.
  if (!isSystemOwner) {
    return <CortexIdentityNoAccess />;
  }

  return <CortexIdentityBody />;
}

function CortexIdentityBody() {
  const queryClient = useQueryClient();
  const t = useT("cortexIdentity");
  const [forbidden, setForbidden] = useState(false);

  const identityQuery = useQuery<CortexIdentity, ApiError>({
    queryKey: ["cortex", "identity"],
    queryFn: getCortexIdentity,
    refetchOnWindowFocus: false,
    retry: false,
    // Poll suave: la reflexión es asíncrona (Celery) — así "lo que sabe de ti"
    // y los rasgos crecen ante tus ojos tras pulsar "Reflexionar ahora".
    refetchInterval: 10_000,
  });

  // Estado editable local — se siembra desde la respuesta del backend.
  const [name, setName] = useState("");
  const [coreValues, setCoreValues] = useState("");
  const [narrative, setNarrative] = useState("");
  const [language, setLanguage] = useState("es");
  const [learningGoals, setLearningGoals] = useState("");
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    const data = identityQuery.data;
    if (!data || seeded) return;
    setName(data.name ?? "");
    setCoreValues(joinLines(data.core_values));
    setNarrative(data.narrative);
    setLanguage(data.language || "es");
    setLearningGoals(joinLines(data.learning_goals));
    setSeeded(true);
  }, [identityQuery.data, seeded]);

  useEffect(() => {
    if (identityQuery.error?.status === 403) setForbidden(true);
  }, [identityQuery.error]);

  const saveMutation = useMutation<CortexIdentity, ApiError, void>({
    mutationFn: () =>
      updateCortexIdentity({
        name: name.trim() || null,
        core_values: parseLines(coreValues),
        narrative,
        language,
        learning_goals: parseLines(learningGoals),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["cortex", "identity"], data);
    },
    onError: (error) => {
      if (error.status === 403) setForbidden(true);
    },
  });

  const reflectMutation = useMutation({
    mutationFn: reflectCortexIdentity,
  });

  if (forbidden) return <CortexIdentityNoAccess />;

  if (identityQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          {t("loading")}
        </p>
      </div>
    );
  }

  const identity = identityQuery.data;
  const pendingOnboarding = identity ? needsOnboarding(identity) : false;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="cortex-identity-header"
      />

      {/* Banner de onboarding pendiente (prominente — codiseño). */}
      {pendingOnboarding ? (
        <Card
          className="mt-4 border-primary/40 bg-primary/5"
          data-testid="cortex-identity-onboarding"
        >
          <CardContent className="flex items-start gap-3 pt-5">
            <Brain className="text-primary mt-0.5 h-5 w-5 shrink-0" />
            <div className="text-sm">
              <p className="font-medium">{t("onboardingTitle")}</p>
              <p className="text-muted-foreground mt-1">{t("onboardingBody")}</p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Copy honesto SIEMPRE visible. */}
      <p
        className="text-muted-foreground mt-4 flex items-start gap-2 text-xs"
        data-testid="cortex-identity-honesty"
      >
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {t("honestyNote")}
      </p>

      {/* Formulario de campos editables por el owner. */}
      <Card className="mt-6">
        <CardContent className="flex flex-col gap-5 pt-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-name">{t("nameLabel")}</Label>
            <Input
              id="cortex-identity-name"
              data-testid="cortex-identity-name"
              value={name}
              maxLength={CORTEX_IDENTITY_LIMITS.name.max}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-values">{t("valuesLabel")}</Label>
            <textarea
              id="cortex-identity-values"
              data-testid="cortex-identity-values"
              value={coreValues}
              rows={4}
              onChange={(e) => setCoreValues(e.target.value)}
              placeholder={t("valuesPlaceholder")}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("narrativeLabel")}</Label>
            <MarkdownTextarea
              data-testid="cortex-identity-narrative"
              value={narrative}
              onChange={setNarrative}
              rows={5}
              placeholder={t("narrativePlaceholder")}
            />
            <p className="text-muted-foreground text-[11px]">{t("narrativeHint")}</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-language">{t("languageLabel")}</Label>
            <div className="w-40">
              <Select
                id="cortex-identity-language"
                data-testid="cortex-identity-language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                {/* Los nombres de idioma van en SU propio idioma (endónimos), no
                    traducidos al idioma del panel: es lo que hace reconocible la
                    opción para quien busca la suya. Por eso no van al diccionario. */}
                {CORTEX_LANGUAGES.map((lng) => (
                  <option key={lng} value={lng}>
                    {lng === "es" ? "Español" : "English"}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-goals">{t("goalsLabel")}</Label>
            <textarea
              id="cortex-identity-goals"
              data-testid="cortex-identity-goals"
              value={learningGoals}
              rows={3}
              onChange={(e) => setLearningGoals(e.target.value)}
              placeholder={t("goalsPlaceholder")}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
            />
          </div>

          {saveMutation.isError ? (
            <p className="text-destructive text-sm" data-testid="cortex-identity-error">
              {saveMutation.error instanceof ApiError
                ? saveMutation.error.body
                : String(saveMutation.error)}
            </p>
          ) : null}
          {saveMutation.isSuccess ? (
            <p className="text-sm text-emerald-600" data-testid="cortex-identity-saved">
              {t("saved", { n: identity?.version ?? 0 })}
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <Button
              data-testid="cortex-identity-save"
              disabled={saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              <Save className="mr-2 h-4 w-4" />
              {pendingOnboarding ? t("createIdentity") : t("save")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Derivados por la reflexión (solo-lectura) + disparo manual. */}
      {identity ? (
        <Card className="mt-6" data-testid="cortex-identity-derived">
          <CardContent className="flex flex-col gap-4 pt-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">{t("traitsTitle")}</h2>
              <span className="text-muted-foreground text-xs">
                {t("versionLabel", { n: identity.version })}
              </span>
            </div>
            <p className="text-muted-foreground text-xs">{t("traitsHint")}</p>
            {/* Radar Big-Five (F3.6): la forma del perfil, no cinco barras sueltas.
                La geometría es pura y testeada (`traitRadarAxes`). */}
            <TraitRadar traits={identity.traits} />

            <div className="flex items-center gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                data-testid="cortex-identity-reflect"
                disabled={reflectMutation.isPending}
                onClick={() => reflectMutation.mutate()}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                {t("reflectNow")}
              </Button>
              {reflectMutation.isSuccess ? (
                <span
                  className="text-muted-foreground text-xs"
                  data-testid="cortex-identity-reflect-ok"
                >
                  {reflectMutation.data.enqueued ? t("reflectQueued") : t("reflectFailed")}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Lo que sabe de ti — el owner-model que deriva la reflexión (solo-lectura). */}
      {identity ? (
        <Card className="mt-6" data-testid="cortex-identity-relationship">
          <CardContent className="flex flex-col gap-3 pt-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">{t("ownerModelTitle")}</h2>
              <span className="text-muted-foreground text-xs">{t("ownerModelHint")}</span>
            </div>
            {Object.keys(identity.relationship_model ?? {}).length === 0 ? (
              <p
                className="text-muted-foreground text-sm"
                data-testid="cortex-identity-relationship-empty"
              >
                {t("ownerModelEmpty")}
              </p>
            ) : (
              <ul
                className="divide-border divide-y"
                data-testid="cortex-identity-relationship-list"
              >
                {Object.entries(identity.relationship_model).map(([key, value]) => (
                  <li key={key} className="flex items-start gap-3 py-2 text-sm">
                    <span className="text-muted-foreground w-40 shrink-0 break-words">{key}</span>
                    <span className="min-w-0 break-words">{value}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : null}

      {/* Timeline de versiones (F3.6): qué tocó cada reflexión, con su diff. */}
      {identity ? (
        <div className="mt-6">
          <IdentityTimeline />
        </div>
      ) : null}
    </div>
  );
}

function CortexIdentityNoAccess() {
  const t = useT("cortexIdentity");
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        data-testid="cortex-identity-header"
      />
      <EmptyState
        data-testid="cortex-identity-no-access"
        icon={Brain}
        title={t("noAccessTitle")}
        description={t("noAccessDescription")}
      />
    </div>
  );
}
