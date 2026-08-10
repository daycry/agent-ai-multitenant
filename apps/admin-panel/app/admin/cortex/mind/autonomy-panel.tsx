"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { ApiError } from "@/lib/api";
import { getCortexAutonomy, setCortexAutonomy, type CortexAutonomy } from "@/lib/cortex";
import { budgetUsageLabel, honestNote } from "@/lib/cortex-curiosity";
import { useLangOptional } from "@/lib/lang-context";

// ---------------------------------------------------------------------------
// Autonomía — kill-switch de los bucles de fondo + gates + budget (ADR 0078)
// ---------------------------------------------------------------------------
export function AutonomyPanel() {
  const lang = useLangOptional();
  const queryClient = useQueryClient();
  const autonomyQuery = useQuery<CortexAutonomy, ApiError>({
    queryKey: ["cortex", "autonomy"],
    queryFn: getCortexAutonomy,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const toggleMutation = useMutation<
    CortexAutonomy,
    ApiError,
    { autonomy_enabled?: boolean; web_enabled?: boolean; browser_enabled?: boolean }
  >({
    mutationFn: setCortexAutonomy,
    onSuccess: (data) => queryClient.setQueryData(["cortex", "autonomy"], data),
  });

  const autonomy = autonomyQuery.data;

  return (
    <Card data-testid="cortex-autonomy-panel">
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Autonomía (bucles de fondo)</h2>
          <span className="text-muted-foreground text-xs">
            Curiosidad y reflexión programadas — con budget y kill-switch (ADR 0078)
          </span>
        </div>
        {autonomyQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            Cargando…
          </p>
        ) : autonomyQuery.isError || !autonomy ? (
          <p className="text-destructive text-sm">No se pudo cargar el estado de autonomía.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                variant={autonomy.autonomy_enabled ? "destructive" : "default"}
                data-testid="cortex-autonomy-toggle"
                disabled={toggleMutation.isPending}
                onClick={() =>
                  toggleMutation.mutate({ autonomy_enabled: !autonomy.autonomy_enabled })
                }
              >
                {autonomy.autonomy_enabled ? "Apagar autonomía" : "Encender autonomía"}
              </Button>
              <span className="text-sm" data-testid="cortex-autonomy-state">
                Estado:{" "}
                <span
                  className={
                    autonomy.autonomy_enabled
                      ? "font-medium text-emerald-600 dark:text-emerald-400"
                      : "text-muted-foreground font-medium"
                  }
                >
                  {autonomy.autonomy_enabled ? "ENCENDIDA" : "APAGADA"}
                </span>
              </span>
              <Button
                size="sm"
                variant="outline"
                data-testid="cortex-web-toggle"
                disabled={toggleMutation.isPending}
                onClick={() => toggleMutation.mutate({ web_enabled: !autonomy.web_enabled })}
              >
                {autonomy.web_enabled ? "Deshabilitar web" : "Habilitar web"}
              </Button>
              <Button
                size="sm"
                variant={autonomy.browser_enabled ? "destructive" : "outline"}
                data-testid="cortex-browser-toggle"
                disabled={toggleMutation.isPending}
                onClick={() =>
                  toggleMutation.mutate({ browser_enabled: !autonomy.browser_enabled })
                }
              >
                {autonomy.browser_enabled ? "Deshabilitar navegador" : "Habilitar navegador"}
              </Button>
              {toggleMutation.isError ? (
                <span className="text-destructive text-xs">No se pudo cambiar el estado.</span>
              ) : null}
            </div>
            <ul className="text-muted-foreground grid gap-1 text-xs sm:grid-cols-2">
              <li>
                Web del córtex:{" "}
                <span className="text-foreground font-medium" data-testid="cortex-web-state">
                  {autonomy.web_enabled ? "habilitada" : "deshabilitada"}
                </span>{" "}
                (búsqueda/lectura web vía egress-proxy con anti-SSRF)
              </li>
              <li>
                Navegador real (ADR 0080):{" "}
                <span className="text-foreground font-medium" data-testid="cortex-browser-state">
                  {autonomy.browser_enabled ? "habilitado" : "deshabilitado"}
                </span>{" "}
                (Playwright sandboxeado; cada sesión la apruebas tú abajo)
              </li>
              <li>
                Búsquedas de curiosidad hoy:{" "}
                <span className="text-foreground font-medium" data-testid="cortex-budget-usage">
                  {budgetUsageLabel(
                    autonomy.budget.searches_today,
                    autonomy.budget.searches_cap,
                    lang,
                  )}
                </span>
              </li>
              <li>
                Umbral del drive de curiosidad:{" "}
                <span className="text-foreground font-medium">
                  {autonomy.curiosity_drive_threshold}
                </span>
              </li>
              <li>
                Circuit-breaker:{" "}
                <span className="text-foreground font-medium">
                  {autonomy.circuit_breaker_open ? "abierto (pausado)" : "cerrado (ok)"}
                </span>
              </li>
            </ul>
            {/* La API manda las dos notas; se muestra la del idioma activo. */}
            <p className="text-muted-foreground text-xs" data-testid="cortex-autonomy-note">
              {honestNote(autonomy, lang)}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
