"use client";

/**
 * Hub de Capacidad por entidad (Plan 06.17 task_06_17_09).
 *
 * El modelo mental ÚNICO SABER/RECORDAR/SER/HACER (training-model.md) encima de
 * `GET /{entity}/{id}/capabilities` (task_06_17_08), que ya compone la sección
 * HACER con `effective-tools` de 06.18. Este componente NO recalcula nada:
 * consume el contrato y delega TODA la lógica de derivación en el módulo puro
 * `@/lib/capability/hub` (testeado aislado en `capability-hub.test.ts`).
 *
 * Principios del plan que materializa:
 *   - **4 secciones** con badge de **estado HONESTO** ("3 KBs asignadas", "sin
 *     memoria de proyecto", "modelo no configurado") — regla 4: nada parece
 *     activo si no lo está.
 *   - **Verbo único** "Asignar / Quitar" ("Editar" para la persona en SER).
 *   - **Nivel explícito** (Rol/Stack/Equipo/Plataforma) por capacidad.
 *   - **Aviso de agente global** (ADR 0054) destacado en cabecera.
 *   - **Checklist** de onboarding con orden Persona → Saber → Hacer → Recordar.
 *
 * Reutiliza el sistema de diseño existente: Card/Badge shadcn, el primitivo
 * Tooltip de 06.18 y la taxonomía de tools de 06.18 para la sección HACER.
 * Read-only: el Hub es una vista de SET EFECTIVO; cada "Asignar/Editar/Quitar"
 * enlaza a la sección concreta de la ficha (que ya existe) donde se edita.
 */

import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Brain,
  CheckCircle2,
  Circle,
  Info,
  ListChecks,
  Sparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { resolveCategory } from "@/lib/tools/taxonomy";
import {
  buildChecklist,
  buildSections,
  globalAgentNotice,
  hubTitle,
  resolveKBLevel,
  type CapabilitiesResponse,
  type CapabilityEntityType,
  type SectionKey,
  type SectionTone,
} from "@/lib/capability/hub";

const SECTION_ICON: Record<SectionKey, LucideIcon> = {
  saber: BookOpen,
  recordar: Brain,
  ser: Sparkles,
  hacer: Wrench,
};

const TONE_VARIANT: Record<SectionTone, BadgeVariant> = {
  success: "success",
  warning: "warning",
  muted: "muted",
  info: "info",
};

interface CapabilityHubProps {
  entityType: CapabilityEntityType;
  entityId: string;
}

export function CapabilityHub({ entityType, entityId }: CapabilityHubProps) {
  const { lang } = useLang();
  const t = useT("capability");

  const { data, isLoading, isError, error } = useQuery<CapabilitiesResponse, ApiError>({
    queryKey: ["capabilities", entityType, entityId],
    queryFn: () => apiFetch<CapabilitiesResponse>(`/${entityType}s/${entityId}/capabilities`),
    enabled: !!entityId,
    refetchOnWindowFocus: false,
  });

  const sections = useMemo(() => (data ? buildSections(data, lang) : []), [data, lang]);
  const checklist = useMemo(() => (data ? buildChecklist(data, lang) : []), [data, lang]);
  const notice = useMemo(() => (data ? globalAgentNotice(data, lang) : null), [data, lang]);

  return (
    <Card data-testid="capability-hub">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> {hubTitle(entityType, lang)}
          </span>
        </CardTitle>
        <p className="text-muted-foreground text-xs">{t("hubDescription")}</p>
      </CardHeader>

      <CardContent className="space-y-4">
        <StateBlock
          isLoading={isLoading}
          isError={isError}
          error={error}
          loadingSkeleton
          skeletonRows={4}
          loadingTestId="capability-hub-loading"
          errorTestId="capability-hub-error"
          errorTitle={t("hubLoadError")}
        >
          {data && (
            <>
              {/* Aviso de agente global (ADR 0054), destacado de primera clase. */}
              {notice && (
                <p
                  className="bg-warning-soft text-warning-soft-foreground rounded p-3 text-xs"
                  data-testid="capability-hub-global-agent-warning"
                  role="status"
                >
                  {notice}
                </p>
              )}

              {/* Checklist de onboarding: Persona → Saber → Hacer → Recordar. */}
              <ChecklistBlock steps={checklist} />

              {/* Las 4 secciones del modelo mental único. */}
              <div
                className="grid grid-cols-1 gap-3 sm:grid-cols-2"
                data-testid="capability-hub-sections"
              >
                {sections.map((section) => (
                  <SectionCard key={section.key} sectionKey={section.key} title={section.title}>
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-muted-foreground text-xs">{section.question}</p>
                      <Badge
                        variant={TONE_VARIANT[section.status.tone]}
                        data-testid={`capability-status-${section.key}`}
                        data-active={section.status.active ? "true" : "false"}
                      >
                        {section.status.badge}
                      </Badge>
                    </div>

                    {/* Cuerpo específico de cada sección. */}
                    {section.key === "saber" && <SaberBody caps={data} />}
                    {section.key === "recordar" && <RecordarBody caps={data} lang={lang} />}
                    {section.key === "ser" && <SerBody caps={data} lang={lang} />}
                    {section.key === "hacer" && <HacerBody caps={data} lang={lang} />}

                    {/* Verbo único de la sección (acción de asignar/editar). */}
                    <p className="text-muted-foreground mt-2 text-xs">
                      <span
                        className="text-foreground font-medium"
                        data-testid={`capability-verb-${section.key}`}
                      >
                        {section.verb}
                      </span>{" "}
                      {verbHint(section.key, lang)}
                    </p>
                  </SectionCard>
                ))}
              </div>
            </>
          )}
        </StateBlock>
      </CardContent>
    </Card>
  );
}

function verbHint(key: SectionKey, lang: "es" | "en"): string {
  const hints: Record<SectionKey, Record<"es" | "en", string>> = {
    saber: {
      es: "conocimiento desde la sección Knowledge Bases.",
      en: "knowledge from the Knowledge Bases section.",
    },
    recordar: {
      es: "el scope de memoria desde la ficha del agente.",
      en: "the memory scope from the agent details.",
    },
    ser: {
      es: "la persona (modelo y prompt) desde la ficha.",
      en: "the persona (model and prompt) from the details.",
    },
    hacer: {
      es: "tools desde la sección Tools del agente.",
      en: "tools from the agent Tools section.",
    },
  };
  return hints[key][lang];
}

function SectionCard({
  sectionKey,
  title,
  children,
}: {
  sectionKey: SectionKey;
  title: string;
  children: ReactNode;
}) {
  const Icon = SECTION_ICON[sectionKey];
  return (
    <section
      className="border-border rounded-lg border p-3"
      data-testid={`capability-section-${sectionKey}`}
    >
      <h4 className="mb-1.5 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Icon className="h-4 w-4" aria-hidden="true" />
        {title}
      </h4>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cuerpos de sección
// ---------------------------------------------------------------------------

function SaberBody({ caps }: { caps: CapabilitiesResponse }) {
  const { lang } = useLang();
  const kbs = caps.saber.knowledge_bases.map((kb) => resolveKBLevel(kb, lang));
  if (kbs.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1.5" data-testid="capability-saber-kbs">
      {kbs.map((kb) => (
        <li key={kb.kb_id} className="flex items-center justify-between gap-2 text-sm">
          <span className="min-w-0 truncate">{kb.name}</span>
          {/* Nivel explícito (Rol/Stack/Plataforma) — regla 3. */}
          <Badge variant="muted" data-testid={`capability-kb-level-${kb.kb_id}`}>
            {kb.levelLabel}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

const MEMORY_SCOPE_LABEL: Record<string, Record<"es" | "en", string>> = {
  private: { es: "Privada", en: "Private" },
  team_shared: { es: "Equipo", en: "Team" },
  project_shared: { es: "Proyecto", en: "Project" },
  global: { es: "Global", en: "Global" },
};

function RecordarBody({ caps, lang }: { caps: CapabilitiesResponse; lang: "es" | "en" }) {
  const rows = caps.recordar.memory;
  if (rows.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1" data-testid="capability-recordar-scopes">
      {rows.map((m) => (
        <li key={m.scope} className="flex items-center justify-between gap-2 text-sm">
          <span>{MEMORY_SCOPE_LABEL[m.scope]?.[lang] ?? m.scope}</span>
          <span className="text-muted-foreground tabular-nums">{m.count}</span>
        </li>
      ))}
    </ul>
  );
}

function SerBody({ caps, lang }: { caps: CapabilitiesResponse; lang: "es" | "en" }) {
  const t = useT("capability");
  const ser = caps.ser;
  if (ser === null || !ser.model_configured) return null;
  return (
    <dl className="mt-2 space-y-1 text-sm" data-testid="capability-ser-detail">
      {ser.provider && (
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground text-xs">{t("fieldProvider")}</dt>
          <dd className="font-medium">{ser.provider}</dd>
        </div>
      )}
      {ser.model && (
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground text-xs">{t("fieldModel")}</dt>
          <dd className="font-medium">{ser.model}</dd>
        </div>
      )}
      {ser.temperature !== null && (
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground text-xs">{t("fieldTemperature")}</dt>
          <dd className="font-medium tabular-nums">{ser.temperature}</dd>
        </div>
      )}
      {ser.model_origin && (
        <div className="flex items-center justify-between gap-2">
          <dt className="text-muted-foreground text-xs">{t("serModelOrigin")}</dt>
          <dd className="font-medium">
            {MODEL_ORIGIN_LABEL[ser.model_origin]?.[lang] ?? ser.model_origin}
          </dd>
        </div>
      )}
    </dl>
  );
}

/** Etiqueta bilingüe del nivel que fija el modelo efectivo (Ola D / ADR 0065). */
const MODEL_ORIGIN_LABEL: Record<string, { es: string; en: string }> = {
  agent: { es: "Agente (propio)", en: "Agent (own)" },
  team: { es: "Equipo", en: "Team" },
  project: { es: "Proyecto", en: "Project" },
  platform: { es: "Plataforma (default)", en: "Platform (default)" },
};

/**
 * HACER incrusta el set efectivo de 06.18: la lista `effective` que el contrato
 * `capabilities` ya compuso con `compute_effective_tools`. Cada tool se muestra
 * con un badge de su FUNCIÓN (taxonomía compartida de 06.18) cuando es conocida;
 * `shell_exec` se destaca por ser privilegiada.
 */
function HacerBody({ caps, lang }: { caps: CapabilitiesResponse; lang: "es" | "en" }) {
  const t = useT("capability");
  const { effective, unrestricted, shell_exec_effective } = caps.hacer;
  if (unrestricted) {
    return (
      <p className="text-muted-foreground mt-2 text-xs" data-testid="capability-hacer-unrestricted">
        {t("hacerUnrestrictedDetail")}
      </p>
    );
  }
  if (effective.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5" data-testid="capability-hacer-tools">
      {effective.map((name) => {
        const fn = resolveCategory(toolCategoryGuess(name), lang);
        const isPrivileged = name === "shell_exec" && shell_exec_effective;
        return (
          <Tooltip key={name} content={fn.help}>
            <TooltipTrigger
              aria-label={`${name} (${fn.labelEs})`}
              data-testid={`capability-hacer-tool-${name}`}
            >
              <Badge
                variant={isPrivileged ? "warning" : "muted"}
                className="gap-1 font-mono text-[11px]"
              >
                {isPrivileged && <Info aria-hidden="true" className="h-3 w-3" />}
                {name}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
        );
      })}
    </div>
  );
}

/**
 * Heurística de FUNCIÓN para una tool por su nombre canónico, solo para el
 * tooltip de la sección HACER. El catálogo real de categorías vive en el
 * backend; aquí basta una pista para el badge informativo (fallback "custom").
 */
function toolCategoryGuess(name: string): string {
  if (name.startsWith("rag_") || name.includes("knowledge") || name.includes("memory")) {
    return "knowledge";
  }
  if (name.startsWith("git_") || name === "git") return "git";
  if (name === "shell_exec" || name.includes("command")) return "command";
  if (name.includes("test") || name.includes("run")) return "runtime";
  if (name.includes("file") || name.includes("read") || name.includes("write")) return "file";
  if (name.includes("http") || name.includes("fetch") || name.includes("network")) return "network";
  if (name.includes("notify") || name.includes("notification")) return "notification";
  return "custom";
}

// ---------------------------------------------------------------------------
// Checklist de onboarding
// ---------------------------------------------------------------------------

function ChecklistBlock({ steps }: { steps: ReturnType<typeof buildChecklist> }) {
  const t = useT("capability");
  if (steps.length === 0) return null;
  return (
    <div className="bg-muted/30 rounded-lg p-3" data-testid="capability-hub-checklist">
      <h4 className="text-muted-foreground mb-2 inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
        <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
        {t("checklistTitle")}
      </h4>
      <ol className="space-y-1.5">
        {steps.map((step) => (
          <li
            key={step.section}
            className="flex items-center gap-2 text-sm"
            data-testid={`capability-checklist-step-${step.section}`}
            data-done={step.done ? "true" : "false"}
          >
            {step.done ? (
              <CheckCircle2
                className="text-success-soft-foreground h-4 w-4 shrink-0"
                aria-hidden="true"
              />
            ) : (
              <Circle className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <span className={step.done ? "text-muted-foreground line-through" : ""}>
              {step.label}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
