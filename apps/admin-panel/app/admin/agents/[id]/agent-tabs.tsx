"use client";

/**
 * Las cinco pestañas de la ficha del agente (`task_ui_32`, plan ui-reestructuracion):
 * Identidad y modelo · Skills y tools · Conocimiento · Memorias · Actividad. Cada pestaña
 * monta sólo lo suyo (las inactivas no se pintan ni piden datos); las secciones de
 * asignación son las de siempre, sin tocar.
 */

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

import { AgentActivityTab } from "./agent-activity-tab";
import type { Agent } from "./agent-detail-types";
import { AgentIdentityTab } from "./agent-identity-tab";
import { AgentKbsSection } from "./agent-kbs-section";
import { AgentMemoriesTab } from "./agent-memories-tab";
import { AgentSkillsSection } from "./agent-skills-section";
import { AgentToolsSection } from "./agent-tools-section";

export function AgentTabs({ agent, isReadOnly }: { agent: Agent; isReadOnly: boolean }) {
  const t = useT("agentTabs");
  return (
    <Tabs defaultValue="identity" className="mt-4" data-testid="agent-tabs">
      <TabsList aria-label={t("tabsLabel")} className="flex-wrap">
        <TabsTrigger value="identity" data-testid="agent-tab-identity">
          {t("identity")}
        </TabsTrigger>
        <TabsTrigger value="skills" data-testid="agent-tab-skills">
          {t("skills")}
        </TabsTrigger>
        <TabsTrigger value="knowledge" data-testid="agent-tab-knowledge">
          {t("knowledge")}
        </TabsTrigger>
        <TabsTrigger value="memories" data-testid="agent-tab-memories">
          {t("memories")}
        </TabsTrigger>
        <TabsTrigger value="activity" data-testid="agent-tab-activity">
          {t("activity")}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="identity" data-testid="agent-panel-identity">
        <AgentIdentityTab agent={agent} />
      </TabsContent>
      <TabsContent value="skills" data-testid="agent-panel-skills" className="space-y-4">
        {/* Plan 06.15: tools asignadas (básicas / avanzadas). */}
        <AgentToolsSection
          agentId={agent.id}
          isReadOnly={isReadOnly}
          projectId={agent.project_id}
        />
        {/* Plan 06.18: skills asignadas (inyectan prompt_fragment). */}
        <AgentSkillsSection agentId={agent.id} isReadOnly={isReadOnly} />
      </TabsContent>
      <TabsContent value="knowledge" data-testid="agent-panel-knowledge">
        {/* Plan 06.9: bases de conocimiento concedidas a este agente. */}
        <AgentKbsSection agentId={agent.id} isReadOnly={isReadOnly} />
      </TabsContent>
      <TabsContent value="memories" data-testid="agent-panel-memories">
        <AgentMemoriesTab agent={agent} />
      </TabsContent>
      <TabsContent value="activity" data-testid="agent-panel-activity">
        <AgentActivityTab agentId={agent.id} />
      </TabsContent>
    </Tabs>
  );
}
