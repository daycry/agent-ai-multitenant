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
 * evoluciona, NO consciencia ni un "yo" real. El copy no insinúa lo contrario.
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
import { useCurrentUser } from "@/lib/use-current-user";

const HONESTY_NOTE =
  "La identidad del córtex es un modelo computacional que evoluciona — no es consciencia ni un “yo” real.";

export default function CortexIdentityPage() {
  const { isSystemOwner, isLoading: userLoading } = useCurrentUser();

  // Mientras no sabemos el rol, nada: nunca parpadear contenido owner-only.
  if (userLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          Cargando…
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
          Cargando identidad…
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
        title="Identidad del córtex"
        description="Co-diseña quién es tu córtex: su nombre, sus valores y su narrativa. Es un modelo computacional que evoluciona, no consciencia."
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
              <p className="font-medium">Aún no le has dado identidad a tu córtex</p>
              <p className="text-muted-foreground mt-1">
                Ponle un nombre y unos valores. A partir de ahí, la reflexión periódica irá puliendo
                su narrativa y sus rasgos con el tiempo.
              </p>
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
        {HONESTY_NOTE}
      </p>

      {/* Formulario de campos editables por el owner. */}
      <Card className="mt-6">
        <CardContent className="flex flex-col gap-5 pt-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-name">Nombre</Label>
            <Input
              id="cortex-identity-name"
              data-testid="cortex-identity-name"
              value={name}
              maxLength={CORTEX_IDENTITY_LIMITS.name.max}
              onChange={(e) => setName(e.target.value)}
              placeholder="Cómo se llama tu córtex (p. ej. «Atlas»)"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-values">Valores (uno por línea)</Label>
            <textarea
              id="cortex-identity-values"
              data-testid="cortex-identity-values"
              value={coreValues}
              rows={4}
              onChange={(e) => setCoreValues(e.target.value)}
              placeholder={"honestidad\ncuriosidad\nrigor"}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Narrativa (en primera persona)</Label>
            <MarkdownTextarea
              data-testid="cortex-identity-narrative"
              value={narrative}
              onChange={setNarrative}
              rows={5}
              placeholder="Quién soy, qué me importa, cómo ayudo al owner…"
            />
            <p className="text-muted-foreground text-[11px]">
              La reflexión periódica reescribe esta narrativa con el tiempo; aquí puedes darle un
              punto de partida.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-language">Idioma</Label>
            <div className="w-40">
              <Select
                id="cortex-identity-language"
                data-testid="cortex-identity-language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                {CORTEX_LANGUAGES.map((lng) => (
                  <option key={lng} value={lng}>
                    {lng === "es" ? "Español" : "English"}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cortex-identity-goals">Objetivos de aprendizaje (uno por línea)</Label>
            <textarea
              id="cortex-identity-goals"
              data-testid="cortex-identity-goals"
              value={learningGoals}
              rows={3}
              onChange={(e) => setLearningGoals(e.target.value)}
              placeholder={"entender mejor mis proyectos\nrecordar mis preferencias"}
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
              Identidad guardada (versión {identity?.version}).
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <Button
              data-testid="cortex-identity-save"
              disabled={saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              <Save className="mr-2 h-4 w-4" />
              {pendingOnboarding ? "Crear identidad" : "Guardar"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Derivados por la reflexión (solo-lectura) + disparo manual. */}
      {identity ? (
        <Card className="mt-6" data-testid="cortex-identity-derived">
          <CardContent className="flex flex-col gap-4 pt-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Rasgos derivados por la reflexión</h2>
              <span className="text-muted-foreground text-xs">versión {identity.version}</span>
            </div>
            <p className="text-muted-foreground text-xs">
              Los rasgos Big-Five y el ánimo base los ajusta la reflexión periódica de forma
              acotada; no se editan a mano.
            </p>
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
                Reflexionar ahora
              </Button>
              {reflectMutation.isSuccess ? (
                <span
                  className="text-muted-foreground text-xs"
                  data-testid="cortex-identity-reflect-ok"
                >
                  {reflectMutation.data.enqueued
                    ? "Reflexión en marcha; los cambios aparecerán en breve."
                    : "No se pudo encolar la reflexión ahora mismo."}
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
              <h2 className="text-sm font-semibold">Lo que sabe de ti</h2>
              <span className="text-muted-foreground text-xs">
                Modelo computacional del owner — lo deriva la reflexión, no se edita a mano
              </span>
            </div>
            {Object.keys(identity.relationship_model ?? {}).length === 0 ? (
              <p
                className="text-muted-foreground text-sm"
                data-testid="cortex-identity-relationship-empty"
              >
                Aún no ha aprendido nada duradero sobre ti. Conversa con el córtex y pulsa
                «Reflexionar ahora»: lo que destile aparecerá aquí (y lo usará en cada turno).
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
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Identidad del córtex"
        data-testid="cortex-identity-header"
      />
      <EmptyState
        data-testid="cortex-identity-no-access"
        icon={Brain}
        title="Identidad no disponible"
        description="La identidad del córtex es exclusiva del System Owner (el dueño del despliegue). Tu cuenta no tiene ese rol."
      />
    </div>
  );
}
