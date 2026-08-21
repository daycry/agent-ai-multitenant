"use client";

/**
 * task_09_07 — Consentimiento granular de permisos de una instalación
 * del marketplace.
 *
 * Una tool/skill community o experimental SIEMPRE pide consentimiento
 * explícito del project_owner por cada permiso solicitado
 * (allowed_domains / allowed_paths / network_policy). Esta pantalla:
 *
 *   - lista cada permiso solicitado por el listing con su estado actual
 *     (PENDIENTE / CONCEDIDO / DENEGADO),
 *   - deja al project_owner APROBAR o DENEGAR cada permiso uno a uno,
 *   - envía el lote de decisiones a `POST .../consent`,
 *   - refleja que la instalación queda DESHABILITADA hasta que TODOS los
 *     permisos requeridos estén concedidos; denegar uno la mantiene
 *     deshabilitada (el backend audita consent / consent_denied).
 *
 * Endpoints backend (routers/marketplace.py, RLS + RBAC):
 *   GET  /marketplace/installations/{id}/permissions  — superficie + estado
 *   POST /marketplace/installations/{id}/consent       — decisiones por permiso
 *
 * Permisos: LEER cualquier miembro del tenant; DECIDIR solo el
 * project_owner (gateado a `tenant_admin` en el backend). Las acciones
 * de aprobar/denegar van envueltas en <RoleGuard min="tenant_admin">.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Rocket, ShieldCheck, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.marketplace (consent)
// --------------------------------------------------------------------------
type ConsentState = "granted" | "denied" | "pending";
type Decision = "grant" | "deny";

interface PermissionStateItem {
  type: string;
  descriptor: Record<string, unknown>;
  state: ConsentState;
}

interface InstallationPermissions {
  installation_id: string;
  listing_id: string;
  status: string;
  consent_required: boolean;
  all_granted: boolean;
  permissions: PermissionStateItem[];
}

/** Las claves del namespace `marketplaceConsent`. */
type ConsentKey = Parameters<Translator<"marketplaceConsent">>[0];

const PERMISSION_LABEL: Record<string, ConsentKey> = {
  allowed_domains: "permAllowedDomains",
  allowed_paths: "permAllowedPaths",
  network_policy: "permNetworkPolicy",
};

// Ayuda inline por tipo de permiso — el riesgo real que se está aprobando.
// `network_policy` refleja la semántica endurecida (ADR 0094 /
// task_prod12_net_01): 'open' ya NO es internet crudo.
const PERMISSION_HELP: Record<string, ConsentKey> = {
  allowed_domains: "helpAllowedDomains",
  allowed_paths: "helpAllowedPaths",
  network_policy: "helpNetworkPolicy",
};

const STATE_BADGE: Record<ConsentState, { variant: BadgeVariant; labelKey: ConsentKey }> = {
  granted: { variant: "success", labelKey: "stateGranted" },
  denied: { variant: "danger", labelKey: "stateDenied" },
  pending: { variant: "warning", labelKey: "statePending" },
};

const STATUS_BADGE: Record<string, { variant: BadgeVariant; labelKey: ConsentKey }> = {
  enabled: { variant: "success", labelKey: "installStatusEnabled" },
  disabled: { variant: "warning", labelKey: "installStatusDisabled" },
  revoked: { variant: "muted", labelKey: "installStatusRevoked" },
};

/** Render a permission descriptor's value as readable text. */
function renderValue(descriptor: Record<string, unknown>): string {
  const value = descriptor.value;
  if (Array.isArray(value)) return value.map((v) => String(v)).join(", ");
  if (value === null || value === undefined) return "—";
  return String(value);
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function InstallationPermissionsPage() {
  const params = useParams<{ id: string }>();
  const installationId = params.id;
  const queryClient = useQueryClient();
  const t = useT("marketplaceDeploy");
  const tc = useT("marketplaceConsent");
  const tCommon = useT("common");
  const errorText = useErrorText();

  const permsQuery = useQuery({
    queryKey: ["installation-permissions", installationId],
    queryFn: () =>
      apiFetch<InstallationPermissions>(`/marketplace/installations/${installationId}/permissions`),
    refetchOnWindowFocus: false,
    enabled: Boolean(installationId),
  });

  // Local staging of per-permission decisions before submit. A permission
  // not in the map keeps its persisted state.
  const [staged, setStaged] = useState<Record<string, Decision>>({});

  const consentMutation = useMutation({
    mutationFn: (decisions: { type: string; decision: Decision }[]) =>
      apiFetch<InstallationPermissions>(`/marketplace/installations/${installationId}/consent`, {
        method: "POST",
        body: { decisions },
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["installation-permissions", installationId], data);
      setStaged({});
    },
  });

  const data = permsQuery.data;
  const stagedCount = Object.keys(staged).length;

  // The effective state of each permission = staged decision (if any),
  // else the persisted state.
  const effectiveState = useMemo(() => {
    const out: Record<string, ConsentState> = {};
    for (const p of data?.permissions ?? []) {
      const decision = staged[p.type];
      out[p.type] = decision ? (decision === "grant" ? "granted" : "denied") : p.state;
    }
    return out;
  }, [data, staged]);

  function setDecision(type: string, decision: Decision) {
    setStaged((prev) => ({ ...prev, [type]: decision }));
  }

  function submit() {
    const decisions = Object.entries(staged).map(([type, decision]) => ({ type, decision }));
    if (decisions.length === 0) return;
    consentMutation.mutate(decisions);
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8" data-testid="consent-page">
      <PageHeader
        icon={<ShieldCheck className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={tc("title")}
        description={tc("description")}
        data-testid="consent-header"
        actions={
          <>
            {data ? (
              <Badge
                variant={STATUS_BADGE[data.status]?.variant ?? "muted"}
                data-testid="consent-install-status"
              >
                {/* Un estado desconocido se muestra CRUDO: es el valor del
                    backend, y un texto inventado esconderia la divergencia. */}
                {STATUS_BADGE[data.status] ? tc(STATUS_BADGE[data.status].labelKey) : data.status}
              </Badge>
            ) : null}
            {/* ADR 0142: consentir es la mitad; la otra —dónde está desplegada—
                vive en la ficha, y sin este enlace no se llega a ella. */}
            <Button asChild variant="outline" size="sm" data-testid="consent-deployments-link">
              <Link href={`/admin/marketplace/installations/${installationId}`}>
                <Rocket className="mr-1 h-3.5 w-3.5" />
                {t("deploymentsTitle")}
              </Link>
            </Button>
          </>
        }
      />

      {permsQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="consent-loading">
          {tCommon("loading")}
        </p>
      ) : permsQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="consent-error">
          {errorText(permsQuery.error)}
        </p>
      ) : data ? (
        <div className="mt-6 space-y-4">
          {!data.consent_required ? (
            <Card data-testid="consent-not-required">
              <CardContent className="py-6 text-sm">
                {tc("notRequiredBefore")} <strong>verified</strong>
                {tc("notRequiredAfter")}
              </CardContent>
            </Card>
          ) : null}

          {data.permissions.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center">
                <p className="text-muted-foreground text-sm italic" data-testid="consent-empty">
                  {tc("empty")}
                </p>
              </CardContent>
            </Card>
          ) : (
            <ul className="space-y-3" data-testid="consent-permission-list">
              {data.permissions.map((perm) => {
                const state = effectiveState[perm.type] ?? perm.state;
                const badge = STATE_BADGE[state];
                const isStaged = staged[perm.type] !== undefined;
                return (
                  <li key={perm.type}>
                    <Card data-testid={`consent-permission-${perm.type}`}>
                      <CardHeader className="flex flex-row items-start justify-between gap-4">
                        <div className="min-w-0">
                          <CardTitle className="flex items-center gap-2 text-base">
                            <span className="truncate">
                              {PERMISSION_LABEL[perm.type]
                                ? tc(PERMISSION_LABEL[perm.type])
                                : perm.type}
                            </span>
                            <Badge
                              variant={badge.variant}
                              data-testid={`consent-state-${perm.type}`}
                            >
                              {tc(badge.labelKey)}
                              {isStaged ? " *" : ""}
                            </Badge>
                          </CardTitle>
                          <p
                            className="text-muted-foreground mt-1 break-all font-mono text-xs"
                            data-testid={`consent-value-${perm.type}`}
                          >
                            {renderValue(perm.descriptor)}
                          </p>
                          {PERMISSION_HELP[perm.type] ? (
                            <p
                              className="text-muted-foreground mt-1 text-xs"
                              data-testid={`consent-help-${perm.type}`}
                            >
                              {tc(PERMISSION_HELP[perm.type])}
                            </p>
                          ) : null}
                        </div>
                        <RoleGuard min="tenant_admin">
                          <div className="flex shrink-0 items-center gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setDecision(perm.type, "grant")}
                              disabled={consentMutation.isPending}
                              data-testid={`consent-grant-${perm.type}`}
                              aria-label={tc("approve")}
                            >
                              <Check className="mr-1 h-3.5 w-3.5" />
                              {tc("approve")}
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setDecision(perm.type, "deny")}
                              disabled={consentMutation.isPending}
                              data-testid={`consent-deny-${perm.type}`}
                              aria-label={tc("deny")}
                            >
                              <X className="mr-1 h-3.5 w-3.5" />
                              {tc("deny")}
                            </Button>
                          </div>
                        </RoleGuard>
                      </CardHeader>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}

          {data.permissions.length > 0 ? (
            <RoleGuard min="tenant_admin">
              <div className="flex items-center justify-between gap-3 border-t pt-4">
                <p className="text-muted-foreground text-xs" data-testid="consent-pending-hint">
                  {stagedCount === 0 ? tc("hintNone") : tc("hintStaged", { n: stagedCount })}
                </p>
                <Button
                  onClick={submit}
                  disabled={stagedCount === 0 || consentMutation.isPending}
                  data-testid="consent-submit"
                >
                  {consentMutation.isPending ? tc("saving") : tc("submit")}
                </Button>
              </div>
            </RoleGuard>
          ) : null}

          {consentMutation.isError ? (
            <p className="text-destructive text-xs" data-testid="consent-submit-error">
              {errorText(consentMutation.error)}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
