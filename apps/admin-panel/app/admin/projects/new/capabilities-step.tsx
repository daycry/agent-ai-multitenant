"use client";

/**
 * Paso «Capacidades» del wizard de proyecto (ADR 0142, `task_mkt2_07`).
 *
 * Puerta 1 de las tres, y la que cierra la decisión D3: al crear un proyecto se
 * ofrece lo que el tenant ya tiene instalado, y lo marcado queda configurado y
 * asignado desde el día 1 — en vez de nacer el proyecto vacío y tener que ir a
 * buscar la capacidad después.
 *
 * ## Por qué el paso va DESPUÉS del de equipo
 *
 * Porque los roles de `targets` se pre-marcan contra el equipo del proyecto, y
 * hasta el paso 2 no se sabe cuál es.
 *
 * ## Por qué no se toca la API de creación de proyectos
 *
 * El wizard **encadena los POST** al endpoint de despliegue después de crear.
 * Meter los despliegues dentro de `POST /projects` habría sido menos vueltas y
 * mucha más superficie: la creación pasaría a poder fallar por una `base_url`
 * mal escrita. Con el encadenado, el proyecto nace igual y lo que no entró se
 * reporta por-item (lo hace `deployCapabilities`, abajo).
 *
 * ## De dónde salen las capacidades
 *
 * NO de `GET /projects/{id}/marketplace/available`: aquí el proyecto todavía no
 * existe, que es literalmente la decisión D2. Se juntan en cliente lo instalado
 * y el catálogo — dos peticiones, no una por instalación.
 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { DeploymentConfigForm } from "@/components/marketplace/deployment-config-form";
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
} from "@/components/marketplace/deployment-types";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

const TRUST_BADGE: Record<string, BadgeVariant> = {
  verified: "success",
  community: "info",
  experimental: "warning",
};

/** Cómo acabó el despliegue de UNA capacidad sobre el proyecto recién creado. */
export interface CapabilityDeployResult {
  installationId: string;
  name: string;
  outcome: "ok" | "already" | "failed";
  warnings: string[];
  oauthPending: boolean;
  error?: string;
}

/**
 * Lo instalado y habilitado del tenant, listo para pintar.
 *
 * Dos consultas y un `join` en cliente. `limit` al tope del backend (500): por
 * encima de eso el paso mostraría un subconjunto sin decirlo, pero un tenant con
 * 500 instalaciones del marketplace no es el problema de hoy y fingir paginación
 * en un checkbox-list sí sería complicar por adelantado.
 */
export function useTenantCapabilities(): AvailableCapability[] {
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

  return useMemo(
    () => capabilitiesFromInstallations(installationsQuery.data ?? [], listingsQuery.data ?? []),
    [installationsQuery.data, listingsQuery.data],
  );
}

/**
 * Despliega, una a una, las capacidades marcadas sobre el proyecto ya creado.
 *
 * En serie y con `try` por item a propósito: el proyecto ya existe, así que un
 * fallo aquí **no puede** deshacerlo ni impedir que las demás entren. Devuelve
 * qué pasó con cada una para que el wizard lo enseñe en vez de redirigir como si
 * todo hubiera ido bien.
 */
export async function deployCapabilities(
  projectId: string,
  capabilities: AvailableCapability[],
  drafts: Record<string, DeploymentDraft>,
  /**
   * El formateador de errores del LLAMANTE, obligatorio y sin default.
   *
   * Esta funcion no es un componente ni un hook, asi que no puede llamar a
   * `useErrorText()` (`react-hooks/rules-of-hooks` lo caza). Y con un default
   * volveria a colarse el cuerpo crudo del backend en la tarjeta de resultados,
   * que es el fallo que `task_prod16_05` cierra: el proximo llamante lo
   * reintroduciria sin enterarse. Mismo criterio que `describeMoveError` en
   * `app/admin/board/page.tsx`.
   */
  errorText: (err: unknown) => string,
  fetcher: typeof apiFetch = apiFetch,
): Promise<CapabilityDeployResult[]> {
  const out: CapabilityDeployResult[] = [];
  for (const capability of capabilities) {
    const draft = drafts[capability.installation_id];
    if (!draft) continue;
    try {
      const response = await fetcher<DeploymentCreateResponse>(
        `/marketplace/installations/${capability.installation_id}/deployments`,
        { method: "POST", body: draftBody(projectId, draft) },
      );
      out.push({
        installationId: capability.installation_id,
        name: capability.name,
        outcome: response.already_deployed ? "already" : "ok",
        warnings: response.warnings ?? [],
        oauthPending: Boolean(response.oauth_pending),
      });
    } catch (err: unknown) {
      out.push({
        installationId: capability.installation_id,
        name: capability.name,
        outcome: "failed",
        warnings: [],
        oauthPending: false,
        error: errorText(err),
      });
    }
  }
  return out;
}

/** ¿Alguna capacidad marcada tiene la config inválida? Bloquea el «Crear». */
export function capabilitiesBlocked(
  capabilities: AvailableCapability[],
  drafts: Record<string, DeploymentDraft>,
): boolean {
  return capabilities.some((capability) => {
    const draft = drafts[capability.installation_id];
    return draft !== undefined && draftErrors(capability, draft).length > 0;
  });
}

export interface CapabilitiesStepProps {
  capabilities: AvailableCapability[];
  drafts: Record<string, DeploymentDraft>;
  onDraftsChange: (next: Record<string, DeploymentDraft>) => void;
}

export function CapabilitiesStep({ capabilities, drafts, onDraftsChange }: CapabilitiesStepProps) {
  const t = useT("marketplaceDeploy");

  function toggle(capability: AvailableCapability) {
    const key = capability.installation_id;
    if (key in drafts) {
      const { [key]: _dropped, ...rest } = drafts;
      onDraftsChange(rest);
      return;
    }
    onDraftsChange({ ...drafts, [key]: initialDraft(capability) });
  }

  if (capabilities.length === 0) {
    return (
      <Card data-testid="wizard-step-capabilities">
        <CardContent className="py-8 text-center">
          <p className="text-muted-foreground text-sm italic" data-testid="capabilities-empty">
            {t("wizardNothingInstalled")}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="wizard-step-capabilities">
      <CardContent className="space-y-4 py-5">
        <p className="text-muted-foreground text-xs" data-testid="capabilities-help">
          {t("wizardStepHelp")}
        </p>
        <ul className="space-y-3" data-testid="capabilities-list">
          {capabilities.map((capability) => {
            const key = capability.installation_id;
            const checked = key in drafts;
            return (
              <li key={key} className="rounded border p-3" data-testid={`capability-${key}`}>
                <label className="flex cursor-pointer items-start gap-2 text-sm">
                  <Checkbox
                    checked={checked}
                    onChange={() => toggle(capability)}
                    data-testid={`capability-check-${key}`}
                  />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2 font-medium">
                      {capability.name}
                      {capability.kind ? <Badge variant="info">{capability.kind}</Badge> : null}
                      <Badge variant="muted">{capability.version}</Badge>
                      {capability.trust_level ? (
                        <Badge variant={TRUST_BADGE[capability.trust_level] ?? "muted"}>
                          {capability.trust_level}
                        </Badge>
                      ) : null}
                    </span>
                    {capability.description ? (
                      <span className="text-muted-foreground block text-xs">
                        {capability.description}
                      </span>
                    ) : null}
                  </span>
                </label>

                {checked ? (
                  <div className="mt-3 border-t pt-3">
                    <DeploymentConfigForm
                      idPrefix={`capability-${key}`}
                      capability={capability}
                      draft={drafts[key]}
                      onChange={(next) => onDraftsChange({ ...drafts, [key]: next })}
                    />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
