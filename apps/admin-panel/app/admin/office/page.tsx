"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { Building2 } from "lucide-react";

import { agentVisualState, stepBubble, type AgentVisualState } from "@/lib/office/mapping";
import { apiFetch } from "@/lib/api";

/**
 * La Oficina v1 (ADR 0118): el tenant como piso 2D donde TODO mapea a
 * telemetría real — mesas = planes con runs activos (GET /runs), banco =
 * agentes sin run (GET /agents), puerta del humano = runs escalados. Es una
 * LENTE sobre las pantallas existentes: clic en un personaje → su run real.
 * Cero estados inventados (principio del ADR); la semántica visual vive en
 * lib/office/mapping (compartida con el Replay, ADR 0119).
 */

interface OfficeRun {
  id: string;
  verdict: string;
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  task_id: string;
  task_title: string | null;
  plan_id: string | null;
  plan_title: string | null;
}

interface OfficeAgent {
  id: string;
  name: string;
  role: string | null;
}

const ROLE_EMOJI: Record<string, string> = {
  project_manager: "🗂️",
  architect: "📐",
  backend_dev: "💻",
  frontend_dev: "🎨",
  qa: "🧪",
  reviewer: "🔍",
  devops: "⚙️",
  security: "🛡️",
  technical_writer: "✍️",
};

const STATE_BADGE: Record<AgentVisualState, string> = {
  idle: "😴",
  working: "⌨️",
  reviewing: "🔍",
  waiting_human: "🚪",
  dizzy: "💫",
  aborted: "🛑",
  done: "✅",
};

function Character({
  agent,
  state,
  bubble,
  onClick,
}: {
  agent: { id: string; name: string; role: string | null };
  state: AgentVisualState;
  bubble?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={`office-agent-${agent.id}`}
      onClick={onClick}
      className="hover:bg-accent flex w-36 flex-col items-center gap-1 rounded-xl p-3 text-center transition-colors"
      title={`${agent.name} — ${state}`}
    >
      {bubble && (
        <span
          data-testid={`office-bubble-${agent.id}`}
          className="bg-card text-card-foreground max-w-40 rounded-2xl border px-3 py-1 text-xs shadow-sm"
        >
          {bubble}
        </span>
      )}
      <span
        className={`text-4xl ${state === "dizzy" ? "animate-spin" : state === "working" ? "animate-pulse" : ""}`}
        aria-hidden="true"
      >
        {ROLE_EMOJI[agent.role ?? ""] ?? "🤖"}
      </span>
      <span className="text-xs font-medium">
        {STATE_BADGE[state]} {agent.name}
      </span>
    </button>
  );
}

export default function OfficePage() {
  const router = useRouter();

  const runningRuns = useQuery({
    queryKey: ["office-runs-running"],
    queryFn: () => apiFetch<OfficeRun[]>("/runs?verdict=running&limit=100"),
    refetchInterval: 5000,
  });
  const escalatedRuns = useQuery({
    queryKey: ["office-runs-escalated"],
    queryFn: () => apiFetch<OfficeRun[]>("/runs?verdict=needs_human_review&limit=20"),
    refetchInterval: 15000,
  });
  const agents = useQuery({
    queryKey: ["office-agents"],
    queryFn: () => apiFetch<OfficeAgent[]>("/agents"),
  });

  const { desks, busyAgentIds } = useMemo(() => {
    const byPlan = new Map<string, { title: string; runs: OfficeRun[] }>();
    const busy = new Set<string>();
    for (const run of runningRuns.data ?? []) {
      const key = run.plan_id ?? "sin-plan";
      const desk = byPlan.get(key) ?? { title: run.plan_title ?? "Sin plan", runs: [] };
      desk.runs.push(run);
      byPlan.set(key, desk);
      if (run.agent_id) busy.add(run.agent_id);
    }
    for (const run of escalatedRuns.data ?? []) {
      if (run.agent_id) busy.add(run.agent_id);
    }
    return { desks: byPlan, busyAgentIds: busy };
  }, [runningRuns.data, escalatedRuns.data]);

  const idleAgents = (agents.data ?? []).filter((a) => !busyAgentIds.has(a.id));

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-8">
      <div className="flex items-center gap-3">
        <Building2 className="h-7 w-7" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">La Oficina</h1>
          <p className="text-muted-foreground text-sm">
            El tenant en vivo: cada estado que ves es telemetría real de los runs — nada es
            decorativo. Clic en un personaje para abrir su trabajo.
          </p>
        </div>
      </div>

      {/* Mesas: un plan con runs activos = una mesa con sus agentes sentados. */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {[...desks.entries()].map(([planId, desk]) => (
          <div
            key={planId}
            data-testid={`office-desk-${planId}`}
            className="bg-card rounded-2xl border p-4 shadow-sm"
          >
            <p className="mb-2 text-sm font-semibold">🗄️ {desk.title}</p>
            <div className="flex flex-wrap gap-2">
              {desk.runs.map((run) => (
                <Character
                  key={run.id}
                  agent={{
                    id: run.agent_id ?? run.id,
                    name: run.agent_name ?? "Agente",
                    role: run.agent_role,
                  }}
                  state={agentVisualState({
                    id: run.id,
                    status: run.verdict,
                    abort_code: null,
                    is_review: false,
                    project_id: null,
                  })}
                  bubble={stepBubble({ kind: "tool_call", summary: run.task_title })}
                  onClick={() => router.push(`/admin/executions/${run.id}`)}
                />
              ))}
            </div>
          </div>
        ))}
        {desks.size === 0 && !runningRuns.isLoading && (
          <p className="text-muted-foreground col-span-full text-sm" data-testid="office-empty">
            Nadie está trabajando ahora mismo — la oficina duerme.
          </p>
        )}
      </section>

      {/* Puerta del humano: runs escalados esperando validación. */}
      <section
        data-testid="office-human-door"
        className="rounded-2xl border border-amber-300/60 bg-amber-50/50 p-4 dark:bg-amber-950/20"
      >
        <p className="mb-2 text-sm font-semibold">🚪 Esperando a un humano</p>
        {(escalatedRuns.data ?? []).length === 0 ? (
          <p className="text-muted-foreground text-sm">Nadie espera validación.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {(escalatedRuns.data ?? []).map((run) => (
              <Character
                key={run.id}
                agent={{
                  id: run.agent_id ?? run.id,
                  name: run.agent_name ?? "Agente",
                  role: run.agent_role,
                }}
                state="waiting_human"
                bubble={stepBubble({ kind: "tool_call", summary: run.task_title })}
                onClick={() => router.push(`/admin/executions/${run.id}`)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Banco: agentes del catálogo sin run activo. */}
      <section data-testid="office-bench" className="bg-muted/40 rounded-2xl border p-4">
        <p className="mb-2 text-sm font-semibold">🛋️ Descansando</p>
        <div className="flex flex-wrap gap-2">
          {idleAgents.map((agent) => (
            <Character
              key={agent.id}
              agent={agent}
              state="idle"
              onClick={() => router.push(`/admin/agents`)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
