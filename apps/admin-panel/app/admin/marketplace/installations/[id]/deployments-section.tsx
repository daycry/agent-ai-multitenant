"use client";

/**
 * «Desplegado en N proyectos» + «Desplegar a…» + retirar (ADR 0142, `task_mkt2_06`).
 *
 * La puerta 2 de las tres, y la única desde la que se ve el mapa completo: qué
 * proyectos tienen esta instalación, con qué versión y en qué estado. Eso es
 * literalmente la promesa del ADR 0142 —«¿dónde está desplegado esto?» es un
 * SELECT— hecha pantalla.
 *
 * Tres decisiones que no son cosméticas:
 *
 * 1. **Un formulario POR PROYECTO**, no uno para el lote. El caso que el modelo
 *    viejo no sabía expresar era exactamente dos proyectos del mismo tenant con
 *    `base_url` distinta para la misma capacidad instalada; un formulario
 *    compartido lo volvería a hacer inexpresable.
 * 2. **Los proyectos que ya lo tienen activo salen deshabilitados.** El backend
 *    es idempotente (UNIQUE parcial + no-op con aviso), pero enseñar la
 *    idempotencia ANTES de pulsar es mejor que explicarla después.
 * 3. **Se despliega proyecto a proyecto y se reporta por-item.** Un fallo en el
 *    tercero no puede borrar que el primero y el segundo sí entraron, y
 *    `already_deployed` no se cuenta como despliegue nuevo.
 *
 * Los `warnings` y el `oauth_pending` que devuelve el servicio se enseñan
 * siempre. Tragárselos convertiría un no-entregado en un 201 con buena cara,
 * que es el modo de fallo que este plan entero existe para cerrar.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, PackageCheck, Rocket } from "lucide-react";

import { DeploymentConfigForm } from "@/components/marketplace/deployment-config-form";
import {
  draftBody,
  draftErrors,
  initialDraft,
  type CapabilityShape,
  type Deployment,
  type DeploymentCreateResponse,
  type DeploymentDraft,
} from "@/components/marketplace/deployment-types";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface ProjectLite {
  id: string;
  name: string;
}

/** Cómo acabó el despliegue en UN proyecto. */
type Outcome = "ok" | "already" | "failed";

interface DeployResult {
  projectId: string;
  outcome: Outcome;
  warnings: string[];
  oauthPending: boolean;
  error?: string;
}

const STATUS_BADGE: Record<string, BadgeVariant> = {
  active: "success",
  disabled: "warning",
  retired: "muted",
};

export interface DeploymentsSectionProps {
  installationId: string;
  /** El `config_schema` y los `targets` con los que se pinta el formulario. */
  capability: CapabilityShape;
}

export function DeploymentsSection({ installationId, capability }: DeploymentsSectionProps) {
  const t = useT("marketplaceDeploy");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const deploymentsKey = ["marketplace-deployments", installationId];
  const deploymentsQuery = useQuery({
    queryKey: deploymentsKey,
    queryFn: () =>
      apiFetch<Deployment[]>(`/marketplace/installations/${installationId}/deployments`),
    refetchOnWindowFocus: false,
    enabled: Boolean(installationId),
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => apiFetch<ProjectLite[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  const deployments = useMemo(() => deploymentsQuery.data ?? [], [deploymentsQuery.data]);
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const projectName = useMemo(() => {
    const out: Record<string, string> = {};
    for (const p of projects) out[p.id] = p.name;
    return out;
  }, [projects]);

  const activeProjectIds = useMemo(
    () => new Set(deployments.filter((d) => d.status === "active").map((d) => d.project_id)),
    [deployments],
  );

  // --- el panel «Desplegar a…»: un borrador por proyecto marcado ----------
  const [picking, setPicking] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, DeploymentDraft>>({});
  const [results, setResults] = useState<DeployResult[]>([]);

  const selected = Object.keys(drafts);
  const blocked = selected.some(
    (projectId) => draftErrors(capability, drafts[projectId]).length > 0,
  );

  function toggleProject(projectId: string) {
    setDrafts((prev) => {
      if (projectId in prev) {
        const { [projectId]: _dropped, ...rest } = prev;
        return rest;
      }
      return { ...prev, [projectId]: initialDraft(capability) };
    });
  }

  const deployMutation = useMutation({
    mutationFn: async (targets: string[]) => {
      const out: DeployResult[] = [];
      // En serie y a propósito: cada proyecto es una transacción propia y un
      // fallo no puede llevarse por delante los que sí entraron.
      for (const projectId of targets) {
        try {
          const response = await apiFetch<DeploymentCreateResponse>(
            `/marketplace/installations/${installationId}/deployments`,
            { method: "POST", body: draftBody(projectId, drafts[projectId]) },
          );
          out.push({
            projectId,
            outcome: response.already_deployed ? "already" : "ok",
            warnings: response.warnings ?? [],
            oauthPending: Boolean(response.oauth_pending),
          });
        } catch (err: unknown) {
          out.push({
            projectId,
            outcome: "failed",
            warnings: [],
            oauthPending: false,
            error: errorText(err),
          });
        }
      }
      return out;
    },
    onSuccess: (out) => {
      setResults(out);
      setDrafts({});
      setPicking(false);
      void queryClient.invalidateQueries({ queryKey: deploymentsKey });
    },
  });

  // --- retirada -----------------------------------------------------------
  const [retiring, setRetiring] = useState<string | null>(null);
  const retireMutation = useMutation({
    mutationFn: (deploymentId: string) =>
      apiFetch<unknown>(`/marketplace/deployments/${deploymentId}/retire`, { method: "POST" }),
    onSuccess: () => {
      setRetiring(null);
      void queryClient.invalidateQueries({ queryKey: deploymentsKey });
    },
  });

  return (
    <Card className="mt-6" data-testid="deployments-section">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Rocket className="h-4 w-4" />
            {t("deploymentsTitle")}
          </CardTitle>
          <p className="text-muted-foreground mt-1 text-xs" data-testid="deployments-count">
            {t("deployedInCount", { n: activeProjectIds.size })}
          </p>
        </div>
        <RoleGuard min="tenant_admin">
          <Button
            size="sm"
            onClick={() => {
              setPicking((prev) => !prev);
              setResults([]);
            }}
            data-testid="deployments-deploy-open"
          >
            <PackageCheck className="mr-1 h-3.5 w-3.5" />
            {t("deployTo")}
          </Button>
        </RoleGuard>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* ------------------------- lo desplegado ------------------------ */}
        {deploymentsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="deployments-loading">
            {t("deployedInCount", { n: 0 })}
          </p>
        ) : deploymentsQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="deployments-error">
            {errorText(deploymentsQuery.error)}
          </p>
        ) : deployments.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="deployments-empty">
            {t("deployedNone")}
          </p>
        ) : (
          <ul className="space-y-2" data-testid="deployments-list">
            {deployments.map((row) => (
              <li
                key={row.id}
                className="border-muted flex flex-wrap items-center justify-between gap-2 rounded border px-3 py-2 text-sm"
                data-testid={`deployment-${row.id}`}
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {projectName[row.project_id] ?? row.project_id}
                  </span>
                  <Badge variant="muted">{row.deployed_version}</Badge>
                  <Badge variant={STATUS_BADGE[row.status] ?? "muted"}>
                    {t(
                      row.status === "active"
                        ? "statusActive"
                        : row.status === "disabled"
                          ? "statusDisabled"
                          : "statusRetired",
                    )}
                  </Badge>
                </span>
                {row.status === "active" ? (
                  <RoleGuard min="tenant_admin">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setRetiring(row.id)}
                      disabled={retireMutation.isPending}
                      data-testid={`deployment-retire-${row.id}`}
                    >
                      <Ban className="mr-1 h-3.5 w-3.5" />
                      {t("retire")}
                    </Button>
                  </RoleGuard>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {/* ------------------------ desplegar a… -------------------------- */}
        {picking ? (
          <div className="space-y-4 border-t pt-4" data-testid="deployments-picker">
            <div>
              <h3 className="text-sm font-semibold">{t("pickProjects")}</h3>
              {projects.length === 0 ? (
                <p className="text-muted-foreground text-xs" data-testid="deployments-no-projects">
                  {t("noProjects")}
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5">
              {projects.map((project) => {
                const already = activeProjectIds.has(project.id);
                return (
                  <label
                    key={project.id}
                    className="flex cursor-pointer items-center gap-1.5 text-sm"
                  >
                    <Checkbox
                      checked={project.id in drafts}
                      disabled={already || deployMutation.isPending}
                      onChange={() => toggleProject(project.id)}
                      data-testid={`deployments-project-${project.id}`}
                    />
                    {project.name}
                    {already ? <Badge variant="muted">{t("alreadyDeployedHere")}</Badge> : null}
                  </label>
                );
              })}
            </div>

            {selected.map((projectId) => (
              <div
                key={projectId}
                className="bg-muted/30 rounded border p-3"
                data-testid={`deploy-card-${projectId}`}
              >
                <p className="mb-2 text-sm font-medium">{projectName[projectId] ?? projectId}</p>
                <DeploymentConfigForm
                  idPrefix={`deploy-${projectId}`}
                  capability={capability}
                  draft={drafts[projectId]}
                  disabled={deployMutation.isPending}
                  onChange={(next) => setDrafts((prev) => ({ ...prev, [projectId]: next }))}
                />
              </div>
            ))}

            <div className="flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPicking(false);
                  setDrafts({});
                }}
                disabled={deployMutation.isPending}
                data-testid="deployments-deploy-cancel"
              >
                {t("cancel")}
              </Button>
              <Button
                size="sm"
                onClick={() => deployMutation.mutate(selected)}
                disabled={selected.length === 0 || blocked || deployMutation.isPending}
                data-testid="deployments-deploy-submit"
              >
                {deployMutation.isPending ? t("deploying") : t("submitDeploy")}
              </Button>
            </div>
          </div>
        ) : null}

        {/* -------------------------- resultados -------------------------- */}
        {results.length > 0 ? (
          <ul className="space-y-2 border-t pt-4" data-testid="deployments-results">
            {results.map((result) => {
              const name = projectName[result.projectId] ?? result.projectId;
              return (
                <li key={result.projectId} className="space-y-1 text-xs">
                  <p
                    data-testid={`deploy-result-${result.projectId}`}
                    data-outcome={result.outcome}
                    className={result.outcome === "failed" ? "text-destructive" : ""}
                  >
                    {result.outcome === "ok"
                      ? t("resultOk", { project: name })
                      : result.outcome === "already"
                        ? t("resultAlready", { project: name })
                        : `${t("resultFailed", { project: name })} ${result.error ?? ""}`}
                  </p>
                  {result.warnings.length > 0 ? (
                    <ul
                      className="text-warning-soft-foreground space-y-0.5 pl-4"
                      data-testid={`deploy-warnings-${result.projectId}`}
                    >
                      {result.warnings.map((warning, index) => (
                        <li key={index}>• {warning}</li>
                      ))}
                    </ul>
                  ) : null}
                  {result.oauthPending ? (
                    <p
                      className="text-warning-soft-foreground pl-4"
                      data-testid={`deploy-oauth-${result.projectId}`}
                    >
                      {t("oauthPending")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </CardContent>

      <ConfirmDialog
        open={retiring !== null}
        onOpenChange={(next) => {
          if (!next) setRetiring(null);
        }}
        title={t("retire")}
        description={t("retireConfirm")}
        confirmLabel={t("retire")}
        cancelLabel={t("cancel")}
        destructive
        pending={retireMutation.isPending}
        onConfirm={() => {
          if (retiring) retireMutation.mutate(retiring);
        }}
      />
    </Card>
  );
}
