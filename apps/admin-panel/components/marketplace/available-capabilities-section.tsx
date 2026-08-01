"use client";

/**
 * «Disponibles en tu tenant» — activación local (ADR 0142, `task_mkt2_08`).
 *
 * Puerta 3 de las tres, y la que cierra la decisión D4: desde la pestaña del
 * proyecto se activa lo que el tenant ya tiene instalado, sin pasar por el
 * marketplace. Escribe **la misma entidad** que las otras dos puertas —el mismo
 * `POST /marketplace/installations/{id}/deployments`, el mismo formulario—, que
 * es exactamente lo que impide que las dos vías enseñen estados distintos.
 *
 * Un solo componente para las dos pestañas del proyecto (MCP y Tools), filtrado
 * por `kind`: dos copias con el filtro cambiado serían la primera divergencia.
 *
 * ## Cómo se sabe qué está YA desplegado aquí
 *
 * No hay endpoint «despliegues de este proyecto» —los hay por instalación—, pero
 * no hace falta inventarlo: `GET /projects/{id}/marketplace/available` está
 * definido como *lo instalado y habilitado del tenant MENOS lo que ya tiene un
 * despliegue ACTIVO aquí*. La resta al revés da lo desplegado, exactamente y sin
 * endpoint nuevo. Lo que no da es el `deployment_id`, así que el enlace va a la
 * ficha de la instalación, que es donde vive el detalle y el botón de retirar.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackagePlus, Store } from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { DeploymentConfigForm } from "./deployment-config-form";
import {
  capabilitiesFromInstallations,
  draftBody,
  draftErrors,
  initialDraft,
  type AvailableCapability,
  type DeploymentCreateResponse,
  type DeploymentDraft,
  type InstallationLite,
  type ListingLite,
} from "./deployment-types";

const TRUST_BADGE: Record<string, BadgeVariant> = {
  verified: "success",
  community: "info",
  experimental: "warning",
};

export interface AvailableCapabilitiesSectionProps {
  projectId: string;
  /** Los `kind` de listing que esta pestaña gobierna (`mcp_server`, `tool`, `skill`). */
  kinds: string[];
}

export function AvailableCapabilitiesSection({
  projectId,
  kinds,
}: AvailableCapabilitiesSectionProps) {
  const t = useT("marketplaceDeploy");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const availableKey = ["project-marketplace-available", projectId];
  const availableQuery = useQuery({
    queryKey: availableKey,
    queryFn: () => apiFetch<AvailableCapability[]>(`/projects/${projectId}/marketplace/available`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  // Sólo para la resta que da «lo ya desplegado aquí» (ver cabecera).
  const installationsQuery = useQuery({
    queryKey: ["marketplace-installations"],
    queryFn: () => apiFetch<InstallationLite[]>("/marketplace/installations?limit=500"),
    refetchOnWindowFocus: false,
  });
  const listingsQuery = useQuery({
    queryKey: ["marketplace-listings"],
    queryFn: () => apiFetch<ListingLite[]>("/marketplace/listings?limit=500"),
    refetchOnWindowFocus: false,
  });

  const kindSet = useMemo(() => new Set(kinds), [kinds]);

  const available = useMemo(
    () => (availableQuery.data ?? []).filter((c) => kindSet.has(c.kind)),
    [availableQuery.data, kindSet],
  );

  const deployedHere = useMemo(() => {
    const availableIds = new Set((availableQuery.data ?? []).map((c) => c.installation_id));
    return capabilitiesFromInstallations(
      installationsQuery.data ?? [],
      listingsQuery.data ?? [],
    ).filter((c) => !availableIds.has(c.installation_id) && kindSet.has(c.kind));
  }, [availableQuery.data, installationsQuery.data, listingsQuery.data, kindSet]);

  // El borrador abierto, si hay alguno. Uno cada vez: activar desde la pestaña
  // del proyecto es una acción puntual, no un lote (para el lote está la ficha).
  const [openId, setOpenId] = useState<string | null>(null);
  const [draft, setDraft] = useState<DeploymentDraft | null>(null);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);

  const openCapability = available.find((c) => c.installation_id === openId) ?? null;

  const activateMutation = useMutation({
    mutationFn: (capability: AvailableCapability) =>
      apiFetch<DeploymentCreateResponse>(
        `/marketplace/installations/${capability.installation_id}/deployments`,
        { method: "POST", body: draftBody(projectId, draft ?? initialDraft(capability)) },
      ),
    onSuccess: () => {
      setOpenId(null);
      setDraft(null);
      setFailed(null);
      void queryClient.invalidateQueries({ queryKey: availableKey });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (err: unknown) => {
      // Un fallo se enseña donde se pulsó, no en un toast que se va.
      if (openId) setFailed({ id: openId, message: errorText(err) });
    },
  });

  function toggleOpen(capability: AvailableCapability) {
    if (openId === capability.installation_id) {
      setOpenId(null);
      setDraft(null);
      return;
    }
    setOpenId(capability.installation_id);
    setDraft(initialDraft(capability));
    setFailed(null);
  }

  const blocked =
    openCapability !== null && draft !== null && draftErrors(openCapability, draft).length > 0;

  return (
    <Card className="mt-8" data-testid="available-capabilities-section">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Store className="h-4 w-4" />
          {t("availableTitle")}
        </CardTitle>
        <p className="text-muted-foreground text-xs">{t("availableHelp")}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {availableQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="available-error">
            {errorText(availableQuery.error)}
          </p>
        ) : available.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="available-empty">
            {t("availableEmpty")}
          </p>
        ) : (
          <ul className="space-y-3" data-testid="available-list">
            {available.map((capability) => {
              const id = capability.installation_id;
              const open = openId === id;
              return (
                <li key={id} className="rounded border p-3" data-testid={`available-${id}`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                        {capability.name}
                        <Badge variant="muted">{capability.version}</Badge>
                        <Badge variant={TRUST_BADGE[capability.trust_level] ?? "muted"}>
                          {capability.trust_level}
                        </Badge>
                      </p>
                      {capability.description ? (
                        <p className="text-muted-foreground text-xs">{capability.description}</p>
                      ) : null}
                    </div>
                    <RoleGuard min="tenant_admin">
                      <Button
                        variant={open ? "outline" : "default"}
                        size="sm"
                        onClick={() => toggleOpen(capability)}
                        data-testid={`available-activate-${id}`}
                      >
                        <PackagePlus className="mr-1 h-3.5 w-3.5" />
                        {open ? t("cancel") : t("activate")}
                      </Button>
                    </RoleGuard>
                  </div>

                  {open && draft ? (
                    <div className="mt-3 space-y-3 border-t pt-3">
                      <DeploymentConfigForm
                        idPrefix={`available-${id}`}
                        capability={capability}
                        draft={draft}
                        disabled={activateMutation.isPending}
                        onChange={setDraft}
                      />
                      {failed?.id === id ? (
                        <p
                          className="text-destructive text-xs"
                          data-testid={`available-error-${id}`}
                        >
                          {failed.message}
                        </p>
                      ) : null}
                      <div className="flex justify-end">
                        <Button
                          size="sm"
                          onClick={() => activateMutation.mutate(capability)}
                          disabled={blocked || activateMutation.isPending}
                          data-testid={`available-submit-${id}`}
                        >
                          {activateMutation.isPending ? t("deploying") : t("submitDeploy")}
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}

        {/* Lo que YA vino del marketplace: las dos vías (D4) enseñan lo mismo. */}
        {deployedHere.length > 0 ? (
          <div className="border-t pt-4">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("deployedHereTitle")}
            </h4>
            <ul className="space-y-1.5" data-testid="deployed-here-list">
              {deployedHere.map((capability) => (
                <li
                  key={capability.installation_id}
                  className="flex flex-wrap items-center justify-between gap-2 text-sm"
                  data-testid={`deployed-here-${capability.installation_id}`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    {capability.name}
                    <Badge variant="muted">{capability.version}</Badge>
                  </span>
                  <Link
                    href={`/admin/marketplace/installations/${capability.installation_id}`}
                    className="text-xs underline"
                  >
                    {t("openInstallation")}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
