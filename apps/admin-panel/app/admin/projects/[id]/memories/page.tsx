"use client";

/**
 * Memoria del proyecto (Plan 06.17 task_06_17_14).
 *
 * La sección RECORDAR de la vista "¿con qué cuenta el proyecto?": lo que el
 * Memorizer y los humanos persisten en el scope `project_shared` de ESTE
 * proyecto. Es la sub-página que el Hub de Capacidad del proyecto enlaza para
 * inspeccionar la memoria sin salir del contexto del proyecto.
 *
 * Consume `GET /memories?project_id={id}&scope=project_shared` (tenant-scoped
 * por RLS; el backend además fija `project_id` al scope project_shared). La
 * honestidad de estado de 06.17 task_06_17_06 se mantiene: una memoria sin
 * embedding muestra "No disponible aún" en vez de fingir "0 similares".
 *
 * Read-only: la creación/borrado vive en la pantalla global de memoria
 * (`/admin/memories`); aquí solo se inspecciona el contexto del proyecto.
 */

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Brain } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { memoryDetectorState } from "@/lib/memory/honesty";
import { renderPlanDraft } from "@/lib/plan-draft-md";

type MemoryType = "episodic" | "semantic";

interface MemoryResponse {
  id: string;
  tenant_id: string;
  scope: string;
  type: MemoryType;
  content: string;
  tags: string[];
  project_id: string | null;
  has_embedding: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * El tipo de memoria, por su clave del diccionario.
 *
 * Antes era el texto castellano directamente. Se guarda la CLAVE y no el texto
 * para que la traducción la resuelva el componente con el idioma activo: un
 * `Record` de literales no puede llamar a un hook.
 */
const TYPE_KEY: Record<MemoryType, "typeEpisodic" | "typeSemantic"> = {
  episodic: "typeEpisodic",
  semantic: "typeSemantic",
};

const TYPE_VARIANT: Record<MemoryType, BadgeVariant> = {
  episodic: "default",
  semantic: "info",
};

export default function ProjectMemoriesPage() {
  const t = useT("projectMemories");
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";

  const query = useQuery<MemoryResponse[], ApiError>({
    queryKey: ["project-memories", projectId],
    queryFn: () =>
      apiFetch<MemoryResponse[]>(`/memories?project_id=${projectId}&scope=project_shared`),
    enabled: !!projectId,
    refetchOnWindowFocus: false,
  });

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-memories-page"
    >
      <ProjectBreadcrumb projectId={projectId} current={t("breadcrumb")} />
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
      />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>{t("cardTitle", { n: query.data?.length ?? "…" })}</CardTitle>
        </CardHeader>
        <CardContent>
          <StateBlock
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            loadingSkeleton
            skeletonRows={3}
            loadingTestId="project-memories-loading"
            errorTestId="project-memories-error"
            errorTitle={t("errorTitle")}
          >
            {(query.data ?? []).length === 0 ? (
              <p
                className="text-muted-foreground text-sm italic"
                data-testid="project-memories-empty"
              >
                {t("empty")}
              </p>
            ) : (
              <ul className="space-y-2" data-testid="project-memories-list">
                {(query.data ?? []).map((m) => (
                  <MemoryRow key={m.id} memory={m} />
                ))}
              </ul>
            )}
          </StateBlock>
        </CardContent>
      </Card>
    </div>
  );
}

function MemoryRow({ memory }: { memory: MemoryResponse }) {
  const t = useT("projectMemories");
  return (
    <li
      className="border-muted rounded border px-3 py-2 text-sm"
      data-testid={`project-memory-${memory.id}`}
      data-type={memory.type}
    >
      <div className="flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-wide">
        <Badge variant="primary">{t("badgeProject")}</Badge>
        <Badge variant={TYPE_VARIANT[memory.type] ?? "muted"}>
          {t(TYPE_KEY[memory.type] ?? "typeSemantic")}
        </Badge>
        {memory.has_embedding ? (
          <Badge variant="success">{t("badgeEmbedding")}</Badge>
        ) : (
          <SimilarUnavailableBadge memoryId={memory.id} />
        )}
        {memory.tags.length > 0 ? (
          <span className="text-muted-foreground ml-1 font-mono">{memory.tags.join(" · ")}</span>
        ) : null}
      </div>
      <div className="mt-1 text-sm">{renderPlanDraft(memory.content)}</div>
    </li>
  );
}

// Honestidad de estado (06.17 task_06_17_06): una memoria sin embedding no
// puede tener "similares"; se marca "No disponible aún" en vez de fingir.
function SimilarUnavailableBadge({ memoryId }: { memoryId: string }) {
  const { lang } = useLang();
  const state = memoryDetectorState(false, lang);
  return (
    <Tooltip content={state.note}>
      <TooltipTrigger>
        <Badge variant="muted" data-testid={`project-memory-similar-unavailable-${memoryId}`}>
          {state.label}
        </Badge>
      </TooltipTrigger>
    </Tooltip>
  );
}
