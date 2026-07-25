"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef } from "react";
import { Building2 } from "lucide-react";

import { OfficeMiniverse } from "@/components/office/office-miniverse";
import { agentVisualState, stepBubble } from "@/lib/office/mapping";
import {
  officeCounts,
  projectAssignedAgents,
  toAgentStatuses,
  type AgentStatus,
  type OfficeAgent,
  type OfficeProject,
  type OfficeRun,
} from "@/lib/office/miniverse-bridge";
import { useRunStepBubbles } from "@/lib/office/use-run-step-bubbles";
import { apiFetch } from "@/lib/api";

/**
 * La Oficina (ADR 0118): el tenant como mundo pixel-art EN VIVO, renderizado por
 * el motor real de miniverse (@miniverse/core, MIT) sobre telemetría REAL. La
 * vista visual la pinta {@link OfficeMiniverse} (canvas del motor); aquí se cargan
 * los datos (GET /runs, /agents), se traducen a estados del motor y se ofrece una
 * lista semántica paralela (sr-only) para accesibilidad, teclado y tests. Clic en
 * un personaje → su run/ficha real (la Oficina es una LENTE, no una app aparte).
 */

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
  // Solo los agentes ASIGNADOS A PROYECTOS pueblan la oficina (petición del
  // operador 2026-07-25). La asignación se resuelve por el EQUIPO del proyecto
  // (`project.team_id`) o por `project_local` — ver projectAssignedAgents. Hace
  // falta la lista de proyectos para conocer esos equipos.
  const projects = useQuery({
    queryKey: ["office-projects"],
    queryFn: () => apiFetch<OfficeProject[]>("/projects"),
  });

  const running = useMemo(() => runningRuns.data ?? [], [runningRuns.data]);
  const escalated = useMemo(() => escalatedRuns.data ?? [], [escalatedRuns.data]);
  // Catálogo EFECTIVO de la oficina: solo los agentes asignados a proyectos.
  const catalog = useMemo(
    () => projectAssignedAgents(agents.data ?? [], projects.data ?? []),
    [agents.data, projects.data],
  );

  const { statuses, runByAgent } = useMemo(
    () => toAgentStatuses({ running, escalated, agents: catalog }),
    [running, escalated, catalog],
  );

  // v2 (ADR 0118): burbujas con el ÚLTIMO PASO real en vivo. Un WS por run activo
  // (con agente) trae su step-summary; enriquece la "tarea" del ciudadano → el
  // motor la pinta en la burbuja. Sin summary aún, cae al task_title del run.
  const runIds = useMemo(() => running.filter((r) => r.agent_id).map((r) => r.id), [running]);
  const liveSummaries = useRunStepBubbles(runIds);
  const enriched = useMemo(
    () =>
      statuses.map((s) => {
        const runId = runByAgent[s.id];
        const live = runId ? liveSummaries[runId] : undefined;
        return live ? { ...s, task: live } : s;
      }),
    [statuses, runByAgent, liveSummaries],
  );

  // Refs para que el motor lea SIEMPRE el último snapshot sin re-montarse.
  const statusesRef = useRef<AgentStatus[]>(enriched);
  const runByAgentRef = useRef<Record<string, string>>(runByAgent);
  statusesRef.current = enriched;
  runByAgentRef.current = runByAgent;

  const getStatuses = useCallback(() => statusesRef.current, []);
  const onSelectAgent = useCallback(
    (agentId: string) => {
      const runId = runByAgentRef.current[agentId];
      router.push(runId ? `/admin/executions/${runId}` : "/admin/agents");
    },
    [router],
  );

  // Agrupación para la lista semántica (misma verdad que el mundo): mesas por
  // plan con runs activos, puerta del humano (escalados), banco (agentes libres).
  const { desks, busyIds } = useMemo(() => {
    const byPlan = new Map<string, { title: string; runs: OfficeRun[] }>();
    const busy = new Set<string>();
    for (const run of running) {
      const key = run.plan_id ?? "sin-plan";
      const desk = byPlan.get(key) ?? { title: run.plan_title ?? "Sin plan", runs: [] };
      desk.runs.push(run);
      byPlan.set(key, desk);
      if (run.agent_id) busy.add(run.agent_id);
    }
    for (const run of escalated) if (run.agent_id) busy.add(run.agent_id);
    return { desks: byPlan, busyIds: busy };
  }, [running, escalated]);

  const idleAgents = catalog.filter((a) => !busyIds.has(a.id));
  const counts = useMemo(() => officeCounts(enriched), [enriched]);

  // Capacidad de la planta, redondeada a tramos de 5 (las bandas de mesas) para
  // que pequeñas variaciones del recuento no remonten la escena.
  const capacity = useMemo(
    () => Math.max(5, Math.ceil(Math.max(enriched.length, 1) / 5) * 5),
    [enriched.length],
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <Building2 className="h-7 w-7" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">La Oficina</h1>
          <p className="text-muted-foreground text-sm">
            El tenant en vivo, renderizado con el motor pixel-art de miniverse sobre telemetría
            real: cada personaje es un agente que camina, se sienta en su mesa al trabajar, va a la
            puerta del humano al escalar y deambula cuando descansa. Clic en un personaje para abrir
            su trabajo.
          </p>
        </div>
      </div>

      {/* HUD gerencial en vivo (datos compartidos del tenant, no privados): quién
          trabaja / revisa / espera a un humano / está libre, de un vistazo. */}
      <div
        className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
        data-testid="office-hud"
      >
        <span title="Agentes ejecutando una tarea">⌨️ {counts.working} trabajando</span>
        <span title="Runs de revisión (reviewer)">🔍 {counts.reviewing} revisando</span>
        <span title="Runs escalados a validación humana">
          🚪 {counts.waiting} esperando a un humano
        </span>
        <span title="Agentes del catálogo sin run activo">😴 {counts.idle} libres</span>
      </div>

      {/* La planta se dimensiona a la plantilla (una mesa por agente). `key` fuerza
          un remonte SOLO cuando cambia el tramo de capacidad (bandas de 5), no en
          cada refresco de datos — así la escena no se reinicia sin motivo. */}
      <OfficeMiniverse
        key={`cap-${capacity}`}
        capacity={capacity}
        getStatuses={getStatuses}
        onSelectAgent={onSelectAgent}
      />

      {desks.size === 0 && !runningRuns.isLoading && (
        <p className="text-muted-foreground text-sm" data-testid="office-empty">
          Nadie está trabajando ahora mismo — la oficina duerme.
        </p>
      )}

      {/* Lista semántica paralela (accesibilidad + teclado + tests): mismo mundo,
          navegable por lector de pantalla. Visualmente oculta; el canvas manda. */}
      <div className="sr-only">
        <h2>Agentes en la oficina</h2>
        <section aria-label="Mesas de trabajo">
          {[...desks.entries()].map(([planId, desk]) => (
            <div key={planId} data-testid={`office-desk-${planId}`}>
              <h3>🗄️ {desk.title}</h3>
              <ul>
                {desk.runs.map((run) => (
                  <SemRun
                    key={run.id}
                    id={run.agent_id ?? run.id}
                    name={run.agent_name ?? "Agente"}
                    state={agentVisualState({
                      id: run.id,
                      status: run.verdict,
                      abort_code: null,
                      is_review: (run.agent_role ?? "") === "reviewer",
                      project_id: null,
                    })}
                    bubble={stepBubble({ kind: "tool_call", summary: run.task_title })}
                    onClick={() => router.push(`/admin/executions/${run.id}`)}
                  />
                ))}
              </ul>
            </div>
          ))}
        </section>
        <section data-testid="office-human-door" aria-label="Puerta del humano">
          <h3>🚪 Esperando a un humano</h3>
          {escalated.length === 0 ? (
            <p>Nadie espera validación.</p>
          ) : (
            <ul>
              {escalated.map((run) => (
                <SemRun
                  key={run.id}
                  id={run.agent_id ?? run.id}
                  name={run.agent_name ?? "Agente"}
                  state="waiting_human"
                  bubble={stepBubble({ kind: "tool_call", summary: run.task_title })}
                  onClick={() => router.push(`/admin/executions/${run.id}`)}
                />
              ))}
            </ul>
          )}
        </section>
        <section data-testid="office-bench" aria-label="Descansando">
          <h3>🛋️ Descansando</h3>
          <ul>
            {idleAgents.map((a) => (
              <SemRun
                key={a.id}
                id={a.id}
                name={a.name}
                state="idle"
                onClick={() => router.push("/admin/agents")}
              />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function SemRun({
  id,
  name,
  state,
  bubble,
  onClick,
}: {
  id: string;
  name: string;
  state: string;
  bubble?: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-testid={`office-agent-${id}`}
        onClick={onClick}
        title={`${name} — ${state}`}
      >
        {name} ({state}){bubble && <span data-testid={`office-bubble-${id}`}> — {bubble}</span>}
      </button>
    </li>
  );
}
