"use client";

/**
 * Hub del agente (Plan 06.6 task_06_6_05 + 06_6_07).
 *
 * Vista detalle/edit de un agente. Lee GET /agents/{id} y permite:
 *   - Editar (campos básicos: name, description, role, system_prompt,
 *     memory_scope, review_capability, max_concurrent_tasks).
 *   - Borrar con confirm-by-name.
 *   - Personalizar (fork) en un proyecto del tenant.
 *
 * Los campos del scope (project_id, scope, forked_from_agent_id)
 * son set-once en el backend — esta UI no los expone como editables.
 *
 * **Partición** (prod-16 `task_prod16_08`): esta pantalla tenía 824 líneas, tres
 * diálogos dentro y un ternario de idioma inline. Los diálogos viven ahora en
 * `agent-edit-dialog.tsx`, `agent-delete-dialog.tsx` y `agent-fork-dialog.tsx`,
 * con los tipos comunes en `agent-detail-types.ts`. Aquí queda la vista.
 */

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Copy, Home, Pencil, Trash2 } from "lucide-react";

import { CapabilityHub } from "@/components/capability/capability-hub";
import { PersonaSection } from "@/components/capability/persona-section";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { privateScopeMemoryWarning } from "@/lib/memory/honesty";
import { resolvePromptSource } from "@/lib/persona/persona";

import { AgentDeleteDialog } from "./agent-delete-dialog";
import { SCOPE_BADGE, type Agent } from "./agent-detail-types";
import { AgentEditDialog } from "./agent-edit-dialog";
import { AgentForkDialog } from "./agent-fork-dialog";
import { AgentKbsSection } from "./agent-kbs-section";
import { AgentSkillsSection } from "./agent-skills-section";
import { AgentToolsSection } from "./agent-tools-section";

export default function AgentHubPage() {
  const t = useT("agents");
  const params = useParams<{ id: string }>();
  const agentId = params?.id ?? "";
  const router = useRouter();
  const queryClient = useQueryClient();
  const { lang } = useLang();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Plan 06.17 task_06_17_12: fork "Personalizar (crear copia)".
  const [forkOpen, setForkOpen] = useState(false);

  const {
    data: agent,
    isLoading,
    isError,
    error,
  } = useQuery<Agent, ApiError>({
    queryKey: ["agent", agentId],
    queryFn: () => apiFetch<Agent>(`/agents/${agentId}`),
    enabled: !!agentId,
    refetchOnWindowFocus: false,
  });

  // Built-in agents cannot be edited/deleted by tenant users — the
  // backend rejects with 403 / 405. We hide the buttons to avoid a
  // misleading affordance.
  const isReadOnly = agent?.scope === "global_builtin";

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8" data-testid="agent-hub">
      <Breadcrumb
        items={[
          { label: t("home"), href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: t("agents"), href: "/admin/agents", icon: <Bot className="h-3.5 w-3.5" /> },
          { label: agent?.name ?? t("agentFallback") },
        ]}
      />
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={agent?.name ?? t("agentFallback")}
        description={agent?.description ?? t("loading")}
        actions={
          agent ? (
            <div className="flex flex-wrap items-center gap-2">
              {/* Plan 06.17 task_06_17_12: "Personalizar (crear copia)" disponible
                  para CUALQUIER agente (incl. built-ins read-only — forkear es la
                  vía para personalizar un built-in). El fork hereda KBs/tools/skills. */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setForkOpen(true)}
                data-testid="agent-fork-button"
              >
                <Copy className="mr-1 h-4 w-4" />
                {t("fork")}
              </Button>
              {!isReadOnly ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditOpen(true)}
                    data-testid="agent-edit-button"
                  >
                    <Pencil className="mr-1 h-4 w-4" />
                    {t("edit")}
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => setDeleteOpen(true)}
                    data-testid="agent-delete-button"
                  >
                    <Trash2 className="mr-1 h-4 w-4" />
                    {t("remove")}
                  </Button>
                </>
              ) : (
                <Badge variant="muted">{t("readOnlyBadge")}</Badge>
              )}
            </div>
          ) : null
        }
      />

      <StateBlock
        isLoading={isLoading}
        loadingSkeleton
        skeletonRows={4}
        loadingTestId="agent-loading"
      />

      {isError && (
        <Card className="p-6" data-testid="agent-error">
          <p className="text-danger-soft-foreground text-sm">
            {t("loadFailed", { detail: error?.message ?? t("unknownError") })}
          </p>
          <Button asChild variant="outline" size="sm" className="mt-3">
            <Link href="/admin/agents">{t("backToCatalog")}</Link>
          </Button>
        </Card>
      )}

      {agent && (
        <Card className="space-y-4 p-6" data-testid="agent-fields">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={SCOPE_BADGE[agent.scope] ?? "muted"}>{agent.scope}</Badge>
            <Badge variant="info">{agent.role}</Badge>
            <Badge variant="muted">{agent.agent_type}</Badge>
            {agent.review_capability && <Badge variant="success">{t("canReview")}</Badge>}
            {agent.is_template && <Badge variant="info">{t("isTemplate")}</Badge>}
            {/* Plan 06.17 task_06_17_12: badge Linked/Forked DERIVADO de
                forked_from_agent_id (no del scope, que mentía). Un agente con
                origen es "Forked"; sin origen es "Linked" (copia de catálogo). */}
            {agent.forked_from_agent_id ? (
              <Badge variant="warning" data-testid="agent-forked-badge">
                Forked
              </Badge>
            ) : (
              <Badge variant="muted" data-testid="agent-linked-badge">
                Linked
              </Badge>
            )}
          </div>

          <div>
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              {t("systemPrompt")} · {lang}
            </p>
            {/* Fuente ÚNICA (Plan 06.17 task_06_17_11): la MISMA que lee la
                tarjeta de la lista (model_config.system_prompts), con fallback
                al campo plano legacy. Cierra la colisión lista vs detalle. */}
            <pre
              className="bg-muted/40 mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded p-3 text-xs"
              data-testid="agent-system-prompt-view"
            >
              {resolvePromptSource(agent.model_config, agent.system_prompt, lang).text ||
                agent.system_prompt}
            </pre>
          </div>

          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
            <Field label={t("memoryScope")} value={agent.memory_scope} />
            <Field label={t("maxConcurrent")} value={String(agent.max_concurrent_tasks)} />
            <Field
              label={t("fieldProject")}
              value={agent.project_id ? agent.project_id.slice(0, 8) : "—"}
            />
          </div>

          {/* Honestidad de estado (Plan 06.17 task_06_17_06): un agente IA con
              memory_scope=private NO memoriza entre runs — el Memorizer hace
              skip silencioso (skip_private). Avisamos en vez de prometer
              recuerdo. Solo aplica a agentes IA (un "agente" humano no memoriza
              vía Memorizer en ningún caso). */}
          {agent.agent_type === "ai" && privateScopeMemoryWarning(agent.memory_scope, lang) ? (
            <p
              className="bg-warning-soft text-warning-soft-foreground rounded p-3 text-xs"
              data-testid="agent-private-memory-warning"
              role="status"
            >
              {privateScopeMemoryWarning(agent.memory_scope, lang)}
            </p>
          ) : null}
        </Card>
      )}

      {/* Plan 06.17 task_06_17_09: Hub de Capacidad (modelo mental único
          SABER/RECORDAR/SER/HACER). Vista del set efectivo REAL por encima de
          las secciones de edición que vienen debajo. */}
      {agent && (
        <div className="mt-4">
          <CapabilityHub entityType="agent" entityId={agent.id} />
        </div>
      )}

      {/* Plan 06.17 task_06_17_11: sección Persona (SER) — proveedor/modelo/
          temperatura del catálogo cerrado + prompt efectivo (rol + modo). */}
      {agent && (
        <div className="mt-4">
          <PersonaSection
            modelConfig={agent.model_config}
            systemPrompt={agent.system_prompt}
            role={agent.role}
          />
        </div>
      )}

      {/* Plan 06.9: knowledge bases granted to this agent template */}
      {agent && (
        <div className="mt-4">
          <AgentKbsSection agentId={agent.id} isReadOnly={isReadOnly} />
        </div>
      )}

      {/* Plan 06.15: tools assigned to this agent (básicas / avanzadas) */}
      {agent && (
        <div className="mt-4">
          <AgentToolsSection
            agentId={agent.id}
            isReadOnly={isReadOnly}
            projectId={agent.project_id}
          />
        </div>
      )}

      {/* Plan 06.18: skills assigned to this agent (inject prompt_fragment) */}
      {agent && (
        <div className="mt-4">
          <AgentSkillsSection agentId={agent.id} isReadOnly={isReadOnly} />
        </div>
      )}

      {agent && (
        <AgentEditDialog
          agent={agent}
          open={editOpen}
          onOpenChange={setEditOpen}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: ["agent", agentId] });
            void queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
            setEditOpen(false);
          }}
        />
      )}

      {agent && (
        <AgentDeleteDialog
          agent={agent}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onDeleted={() => {
            setDeleteOpen(false);
            router.push("/admin/agents");
          }}
        />
      )}

      {agent && (
        <AgentForkDialog
          agent={agent}
          open={forkOpen}
          onOpenChange={setForkOpen}
          onForked={(newId) => {
            setForkOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
            router.push(`/admin/agents/${newId}`);
          }}
        />
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}
