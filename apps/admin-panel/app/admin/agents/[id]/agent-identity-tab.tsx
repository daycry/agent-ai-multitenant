"use client";

/**
 * Pestaña «Identidad y modelo» de la ficha del agente (`task_ui_32`): campos básicos, el
 * prompt efectivo, el Hub de Capacidad y la Persona (proveedor/modelo/temperatura). Es la
 * pestaña con la que se abre la ficha, así que conserva los `data-testid` de siempre.
 */

import { CapabilityHub } from "@/components/capability/capability-hub";
import { PersonaSection } from "@/components/capability/persona-section";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { resolvePromptSource } from "@/lib/persona/persona";

import { SCOPE_BADGE, type Agent } from "./agent-detail-types";

export function AgentIdentityTab({ agent }: { agent: Agent }) {
  const t = useT("agents");
  const { lang } = useLang();
  return (
    <>
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
      </Card>

      <div className="mt-4">
        <CapabilityHub entityType="agent" entityId={agent.id} />
      </div>
      <div className="mt-4">
        <PersonaSection
          modelConfig={agent.model_config}
          systemPrompt={agent.system_prompt}
          role={agent.role}
        />
      </div>
    </>
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
