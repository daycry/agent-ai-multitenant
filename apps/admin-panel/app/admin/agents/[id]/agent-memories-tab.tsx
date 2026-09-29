"use client";

/**
 * Pestaña Memorias de la ficha del agente (`task_ui_32`): las últimas memorias que ESTE
 * agente escribió (`GET /memories?agent_id=`, que ya respeta el ámbito privado: sólo su
 * dueño ve las privadas) y el enlace a la pantalla de memorias del tenant. Nada de
 * procedencia inventada: se enseña lo que el endpoint devuelve.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { privateScopeMemoryWarning } from "@/lib/memory/honesty";
import { fmtRunWhen } from "@/lib/runs";

import type { Agent } from "./agent-detail-types";

interface MemoryRow {
  id: string;
  scope: string;
  category: string;
  content: string;
  project_name: string | null;
  created_at: string;
}

const LIMIT = 10;

export function AgentMemoriesTab({ agent }: { agent: Agent }) {
  const t = useT("agentTabs");
  const { lang } = useLang();
  const memories = useQuery({
    queryKey: ["agent-memories", agent.id],
    queryFn: () => apiFetch<MemoryRow[]>(`/memories?agent_id=${agent.id}&limit=${LIMIT}`),
    refetchOnWindowFocus: false,
  });
  const warning =
    agent.agent_type === "ai" ? privateScopeMemoryWarning(agent.memory_scope, lang) : null;
  const rows = memories.data ?? [];

  return (
    <Card className="space-y-3 p-6" data-testid="agent-memories-tab">
      <p className="text-muted-foreground text-sm">
        {t("memoriesIntro", { scope: agent.memory_scope })}
      </p>
      {/* Honestidad de estado (Plan 06.17 task_06_17_06): un agente IA con
          memory_scope=private NO memoriza entre runs. */}
      {warning ? (
        <p
          className="bg-warning-soft text-warning-soft-foreground rounded p-3 text-xs"
          data-testid="agent-private-memory-warning"
          role="status"
        >
          {warning}
        </p>
      ) : null}
      {memories.isError ? (
        <p className="text-danger-soft-foreground text-sm" data-testid="agent-memories-error">
          {t("memoriesLoadFailed")}
        </p>
      ) : rows.length === 0 && !memories.isLoading ? (
        <p className="text-muted-foreground text-sm italic" data-testid="agent-memories-empty">
          {t("memoriesEmpty")}
        </p>
      ) : (
        <ul className="space-y-2" data-testid="agent-memories-list">
          {rows.map((m) => (
            <li key={m.id} className="border-border rounded-md border p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge variant="info">{m.scope}</Badge>
                <Badge variant="muted">{m.category}</Badge>
                <span className="text-muted-foreground text-xs">
                  {m.project_name ?? t("memoryUnknownProject")} · {fmtRunWhen(m.created_at)}
                </span>
              </div>
              <p className="whitespace-pre-wrap break-words">{m.content}</p>
            </li>
          ))}
        </ul>
      )}
      <Button asChild variant="outline" size="sm">
        <Link href="/admin/memories" data-testid="agent-memories-link">
          {t("memoriesLink")}
        </Link>
      </Button>
    </Card>
  );
}
