"use client";

import { useQuery } from "@tanstack/react-query";
import { Trophy } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";

/**
 * Leaderboard de configuraciones (ADR 0121): ¿qué combinación modelo×agente
 * converge más y más barato EN ESTE tenant? Agregación de solo-lectura del
 * backend (GET /runs/leaderboard, umbral n≥5, ventana 90 días) — el orden
 * (éxito desc, coste asc) lo decide el servidor. La atribución es la del
 * run tal como se persistió (nota honesta obligatoria).
 */

interface LeaderboardRow {
  model: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_role: string | null;
  runs: number;
  done: number;
  escalated: number;
  aborted: number;
  success_rate: number;
  avg_iterations: number;
  avg_cost_usd: number;
  avg_tokens: number;
}

export default function LeaderboardPage() {
  const rows = useQuery({
    queryKey: ["runs-leaderboard"],
    queryFn: () => apiFetch<LeaderboardRow[]>("/runs/leaderboard"),
  });

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <Trophy className="h-7 w-7" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Rendimiento de agentes</h1>
          <p className="text-muted-foreground text-sm">
            Qué combinación modelo × agente converge más y más barato con TU carga real (ventana 90
            días, mínimo 5 runs por combinación).
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="pt-4">
          {rows.isLoading && <Spinner className="h-5 w-5" />}
          {!rows.isLoading && (rows.data ?? []).length === 0 && (
            <p className="text-muted-foreground text-sm" data-testid="leaderboard-empty">
              Aún no hay combinaciones con muestras suficientes (se necesitan al menos 5 runs por
              modelo × agente en la ventana).
            </p>
          )}
          {(rows.data ?? []).length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground border-b text-left">
                    <th className="py-2 pr-4">#</th>
                    <th className="py-2 pr-4">Modelo</th>
                    <th className="py-2 pr-4">Agente</th>
                    <th className="py-2 pr-4">Éxito</th>
                    <th className="py-2 pr-4">Runs</th>
                    <th className="py-2 pr-4">Escalados</th>
                    <th className="py-2 pr-4">Abortados</th>
                    <th className="py-2 pr-4">Iter. medias</th>
                    <th className="py-2 pr-4">Coste medio</th>
                  </tr>
                </thead>
                <tbody>
                  {(rows.data ?? []).map((row, i) => (
                    <tr
                      key={`${row.model}-${row.agent_id}`}
                      data-testid={`leaderboard-row-${i}`}
                      className="border-b last:border-0"
                    >
                      <td className="py-2 pr-4">{i === 0 ? "🥇" : i === 1 ? "🥈" : i + 1}</td>
                      <td className="py-2 pr-4 font-mono text-xs">{row.model ?? "—"}</td>
                      <td className="py-2 pr-4">{row.agent_name ?? "—"}</td>
                      <td className="py-2 pr-4 font-semibold">
                        {Math.round(row.success_rate * 100)}%
                      </td>
                      <td className="py-2 pr-4">{row.runs}</td>
                      <td className="py-2 pr-4">{row.escalated}</td>
                      <td className="py-2 pr-4">{row.aborted}</td>
                      <td className="py-2 pr-4">{row.avg_iterations.toFixed(1)}</td>
                      <td className="py-2 pr-4">${row.avg_cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p
            className="text-muted-foreground mt-4 text-xs"
            data-testid="leaderboard-attribution-note"
          >
            Nota: cada run cuenta con la configuración con la que corrió; si cambiaste la persona o
            las skills de un agente después, las filas antiguas reflejan la configuración anterior.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
