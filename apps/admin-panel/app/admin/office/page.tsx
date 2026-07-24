"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useMemo } from "react";
import { Building2 } from "lucide-react";

import { OfficeCanvas } from "@/components/office/office-canvas";
import {
  buildWorld,
  STATE_BADGE,
  stateLabel,
  type Citizen,
  type OfficeAgent,
  type OfficeRun,
} from "@/lib/office/world";
import { apiFetch } from "@/lib/api";

/**
 * La Oficina (ADR 0118): el tenant como piso 2D EN VIVO, réplica del sistema
 * miniverse (canvas + sprites + animación) pero sobre telemetría REAL — cero
 * estados inventados. Mesas = planes con runs activos (GET /runs), puerta del
 * humano = runs escalados, sofá = agentes sin run (GET /agents). El canvas es la
 * vista; una lista semántica paralela (sr-only) da accesibilidad, teclado y es la
 * superficie de test. Clic en un personaje → su run/ficha real (es una LENTE).
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

  const world = useMemo(
    () =>
      buildWorld({
        running: runningRuns.data ?? [],
        escalated: escalatedRuns.data ?? [],
        agents: agents.data ?? [],
      }),
    [runningRuns.data, escalatedRuns.data, agents.data],
  );

  const select = useCallback(
    (c: Citizen) => {
      if (c.runId) router.push(`/admin/executions/${c.runId}`);
      else router.push("/admin/agents");
    },
    [router],
  );

  const doorCitizens = world.citizens.filter((c) => c.zone === "door");
  const loungeCitizens = world.citizens.filter((c) => c.zone === "lounge");

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <Building2 className="h-7 w-7" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">La Oficina</h1>
          <p className="text-muted-foreground text-sm">
            El tenant en vivo: cada personaje es un agente y cada estado que ves es telemetría real
            de sus runs — nada es decorativo. Se sienta en la mesa de su plan al trabajar, va a la
            puerta del humano al escalar y al sofá cuando descansa. Clic en un personaje para abrir
            su trabajo.
          </p>
        </div>
      </div>

      {/* Vista viva: el piso 2D en canvas (miniverse sobre telemetría real). */}
      <OfficeCanvas world={world} onSelect={select} />

      {world.desks.length === 0 && !runningRuns.isLoading && (
        <p className="text-muted-foreground text-sm" data-testid="office-empty">
          Nadie está trabajando ahora mismo — la oficina duerme.
        </p>
      )}

      {/* Capa semántica paralela (accesibilidad + teclado + tests): mismo mundo,
          navegable por lector de pantalla. Visualmente oculta; el canvas manda. */}
      <div className="sr-only">
        <h2>Agentes en la oficina</h2>
        <section aria-label="Mesas de trabajo">
          {world.desks.map((desk) => (
            <div key={desk.id} data-testid={`office-desk-${desk.id}`}>
              <h3>🗄️ {desk.title}</h3>
              <ul>
                {world.citizens
                  .filter((c) => c.zone === "desk" && c.deskId === desk.id)
                  .map((c) => (
                    <SemanticCitizen key={c.key} c={c} onSelect={select} />
                  ))}
              </ul>
            </div>
          ))}
        </section>
        <section data-testid="office-human-door" aria-label="Puerta del humano">
          <h3>🚪 Esperando a un humano</h3>
          {doorCitizens.length === 0 ? (
            <p>Nadie espera validación.</p>
          ) : (
            <ul>
              {doorCitizens.map((c) => (
                <SemanticCitizen key={c.key} c={c} onSelect={select} />
              ))}
            </ul>
          )}
        </section>
        <section data-testid="office-bench" aria-label="Descansando">
          <h3>🛋️ Descansando</h3>
          <ul>
            {loungeCitizens.map((c) => (
              <SemanticCitizen key={c.key} c={c} onSelect={select} />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function SemanticCitizen({ c, onSelect }: { c: Citizen; onSelect: (c: Citizen) => void }) {
  return (
    <li>
      <button
        type="button"
        data-testid={`office-agent-${c.id}`}
        onClick={() => onSelect(c)}
        title={`${c.name} — ${stateLabel(c.state)}`}
      >
        {STATE_BADGE[c.state]} {c.name} ({stateLabel(c.state)})
        {c.bubble && <span data-testid={`office-bubble-${c.id}`}> — {c.bubble}</span>}
      </button>
    </li>
  );
}
