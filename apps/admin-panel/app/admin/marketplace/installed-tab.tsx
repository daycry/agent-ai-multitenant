"use client";

/**
 * Pestaña «Instaladas» del marketplace (`task_09_18`, ampliada por `task_ui_32`):
 * consentimiento, revocar y desinstalar, más lo que dice DÓNDE está la instalación:
 * el nombre del listing (no el UUID), por dónde llega su capacidad (`task_mk_10`) y en
 * qué proyectos está desplegada. «Desplegar en un proyecto» lleva a Capacidades del
 * proyecto elegido, que es donde el despliegue se hace (ADR 0142).
 */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Rocket, ShieldCheck, Trash2 } from "lucide-react";

import { INSTALLATIONS_KEY, INSTALLATIONS_PATH } from "./catalog-updates";
import type { MarketplaceInstallation } from "./marketplace-types";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

/**
 * Variante + CLAVE del diccionario por estado de instalacion.
 *
 * El mapa se conserva para que TypeScript siga exigiendo una entrada por
 * estado; lo que cambia es que lleva la clave y no el texto.
 */
const STATUS_BADGE: Record<
  string,
  { variant: BadgeVariant; labelKey: Parameters<Translator<"marketplace">>[0] }
> = {
  enabled: { variant: "success", labelKey: "installStatusEnabled" },
  disabled: { variant: "warning", labelKey: "installStatusDisabled" },
  revoked: { variant: "muted", labelKey: "installStatusRevoked" },
  // `task_mk_00`: los dos estados que faltaban. Existen en el enum del backend
  // (`db/marketplace.py`) y llegan por la API; sin entrada aquí se pintaban en
  // crudo, con el valor del enum en inglés técnico.
  analyzing: { variant: "info", labelKey: "installStatusAnalyzing" },
  blocked: { variant: "danger", labelKey: "installStatusBlocked" },
};

export function InstalledTab() {
  const t = useT("marketplace");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const projects = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => apiFetch<{ id: string; name: string }[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  // Misma clave y misma ruta que el aviso de actualización (`catalog-updates`):
  // una sola petición para las dos lecturas, y ningún sitio donde el `limit`
  // pueda discrepar.
  const installedQuery = useQuery({
    queryKey: INSTALLATIONS_KEY,
    queryFn: () => apiFetch<MarketplaceInstallation[]>(INSTALLATIONS_PATH),
    refetchOnWindowFocus: false,
  });

  const revokeMutation = useMutation({
    mutationFn: (installationId: string) =>
      apiFetch<MarketplaceInstallation>(`/marketplace/installations/${installationId}/revoke`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-installations"] });
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: (installationId: string) =>
      apiFetch<void>(`/marketplace/installations/${installationId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-installations"] });
    },
  });

  if (installedQuery.isLoading) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="installed-loading">
        {tCommon("loading")}
      </p>
    );
  }
  if (installedQuery.isError) {
    return (
      <p className="text-destructive text-sm" data-testid="installed-error">
        {errorText(installedQuery.error)}
      </p>
    );
  }

  const installations = installedQuery.data ?? [];
  if (installations.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-muted-foreground text-sm italic" data-testid="installed-empty">
            {t("installedEmpty")}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3" data-testid="installed-list">
      {installations.map((install) => {
        const known = STATUS_BADGE[install.status];
        // Un estado que este mapa no conoce se muestra CRUDO a proposito: es el
        // valor del backend, y traducirlo a un texto inventado esconderia la
        // divergencia en vez de enseniarla.
        // task_mk_10 (ADR 0081 reabierto): `enabled` de un tipo diferido no es
        // una capacidad viva — el backend lo dice en `capability` y aquí no se
        // pinta como «Habilitada».
        const deferred = install.status === "enabled" && install.capability === "deferred";
        const statusVariant = deferred
          ? ("warning" as BadgeVariant)
          : (known?.variant ?? ("muted" as BadgeVariant));
        const statusLabel = deferred
          ? t("installStatusDeferred")
          : known
            ? t(known.labelKey)
            : install.status;
        const isRevoked = install.status === "revoked";
        return (
          <Card key={install.id} data-testid={`installed-${install.id}`}>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div className="min-w-0">
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                  <span
                    className={install.listing_name ? "truncate" : "truncate font-mono text-sm"}
                    title={install.listing_id}
                    data-testid={`installed-listing-name-${install.id}`}
                  >
                    {install.listing_name ?? install.listing_id}
                  </span>
                  <Badge variant="muted">{install.version}</Badge>
                  <Badge
                    variant={statusVariant}
                    title={deferred ? (install.capability_reason ?? undefined) : undefined}
                    data-testid={`installed-status-${install.id}`}
                  >
                    {statusLabel}
                  </Badge>
                </CardTitle>
                {deferred ? (
                  <p
                    className="text-warning-soft-foreground mt-1 text-xs"
                    data-testid={`installed-deferred-${install.id}`}
                  >
                    {t("installDeferredHelp")}
                  </p>
                ) : null}
                <CapabilityLine install={install} />
                <DeploymentsLine
                  install={install}
                  deferred={deferred}
                  projects={projects.data ?? []}
                />
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-1">
                <Button
                  asChild
                  variant="outline"
                  size="sm"
                  data-testid={`installed-consent-${install.id}`}
                >
                  <Link href={`/admin/marketplace/installations/${install.id}/permissions`}>
                    <ShieldCheck className="mr-1 h-3.5 w-3.5" />
                    {t("permissions")}
                  </Link>
                </Button>
                <RoleGuard min="tenant_admin">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => revokeMutation.mutate(install.id)}
                    disabled={isRevoked || revokeMutation.isPending}
                    data-testid={`installed-revoke-${install.id}`}
                    aria-label={t("revoke")}
                  >
                    <Ban className="mr-1 h-3.5 w-3.5" />
                    {t("revoke")}
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => uninstallMutation.mutate(install.id)}
                    disabled={uninstallMutation.isPending}
                    data-testid={`installed-uninstall-${install.id}`}
                    aria-label={t("uninstall")}
                  >
                    <Trash2 className="mr-1 h-3.5 w-3.5" />
                    {t("uninstall")}
                  </Button>
                </RoleGuard>
              </div>
            </CardHeader>
          </Card>
        );
      })}

      {revokeMutation.isError ? (
        <p className="text-destructive text-xs" data-testid="installed-revoke-error">
          {errorText(revokeMutation.error)}
        </p>
      ) : null}
      {uninstallMutation.isError ? (
        <p className="text-destructive text-xs" data-testid="installed-uninstall-error">
          {errorText(uninstallMutation.error)}
        </p>
      ) : null}
    </div>
  );
}

/** Por dónde llega la capacidad de la instalación (`task_mk_10`); lo diferido ya lo dice el badge. */
function CapabilityLine({ install }: { install: MarketplaceInstallation }) {
  const t = useT("marketplaceInstalled");
  const label =
    install.capability === "on_deploy"
      ? t("capabilityOnDeploy")
      : install.capability === "catalog_row"
        ? t("capabilityCatalogRow")
        : null;
  if (label === null) return null;
  return (
    <p
      className="text-muted-foreground mt-1 text-xs"
      data-testid={`installed-capability-${install.id}`}
    >
      {label}
    </p>
  );
}

/**
 * Dónde está desplegada (`deployments` del endpoint, sin UUID) y el paso «Desplegar en un
 * proyecto», que navega a Capacidades del proyecto elegido. Un tipo diferido no produce
 * nada que desplegar, así que no ofrece la acción.
 */
function DeploymentsLine({
  install,
  deferred,
  projects,
}: {
  install: MarketplaceInstallation;
  deferred: boolean;
  projects: { id: string; name: string }[];
}) {
  const t = useT("marketplaceInstalled");
  const router = useRouter();
  const [projectId, setProjectId] = useState("");
  const deployments = install.deployments ?? [];
  const canDeploy = !deferred && install.status === "enabled";
  return (
    <div className="mt-2 space-y-2 text-xs">
      {deployments.length === 0 ? (
        <p className="text-muted-foreground" data-testid={`installed-no-deployments-${install.id}`}>
          {t("notDeployed")}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-muted-foreground">{t("deployedIn")}</span>
          {deployments.map((d) => (
            <Link
              key={d.project_id}
              href={`/admin/projects/${d.project_id}/capabilities`}
              data-testid={`installed-deployment-${install.id}-${d.project_id}`}
            >
              <Badge variant={d.status === "active" ? "success" : "warning"}>
                {d.project_name}
                {d.status === "active" ? "" : ` · ${t("deploymentDisabled")}`}
              </Badge>
            </Link>
          ))}
        </div>
      )}
      {canDeploy ? (
        <RoleGuard min="tenant_admin">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              aria-label={t("deployProjectLabel")}
              className="border-border bg-background rounded-md border px-2 py-1 text-xs"
              data-testid={`installed-deploy-project-${install.id}`}
            >
              <option value="">{t("deployPick")}</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              size="sm"
              disabled={projectId === ""}
              onClick={() => router.push(`/admin/projects/${projectId}/capabilities`)}
              data-testid={`installed-deploy-${install.id}`}
            >
              <Rocket className="mr-1 h-3.5 w-3.5" />
              {t("deployAction")}
            </Button>
          </div>
        </RoleGuard>
      ) : null}
    </div>
  );
}
