"use client";

/**
 * ADR 0134 (opción C) — administración de invitaciones.
 *
 * El registro público está cerrado: `POST /auth/register` solo da de alta al
 * PRIMER usuario de una instalación (la puerta de arranque) o a quien presente
 * una invitación válida. Esta pantalla es, por tanto, la única vía de producto
 * para que entre alguien nuevo.
 *
 * Lo que gobierna el diseño de la pantalla es una propiedad del backend: el
 * token en claro se devuelve **una sola vez**, al emitir, y no se persiste en
 * ninguna parte (en la BD solo vive su SHA-256). De ahí que:
 *
 *   - el diálogo posterior a la emisión enseñe el valor con un aviso explícito
 *     de que no se podrá recuperar;
 *   - se ofrezca ya montado el enlace `/accept-invite?token=…` que el admin
 *     tiene que hacer llegar al invitado — sin eso el admin se queda con un
 *     secreto y sin instrucciones;
 *   - la tabla enseñe únicamente el `token_prefix`, porque el listado del
 *     backend no trae (ni puede traer) el valor.
 *
 * Endpoints:
 *   GET  /admin/tenants                        — tenants para el selector
 *   GET  /admin/invitations                    — listado (sin token)
 *   POST /admin/invitations                    — emitir (201, con token)
 *   POST /admin/invitations/{id}/revoke        — revocar
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, ShieldAlert, Ticket, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/api";
import { useT, type MessageKey } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types — espejo de api_server.schemas.invitations.
// ---------------------------------------------------------------------------
interface Tenant {
  id: string;
  name: string;
  slug: string;
}

interface Invitation {
  id: string;
  tenant_id: string;
  tenant_name: string | null;
  email: string;
  role: string;
  token_prefix: string;
  status: "pending" | "redeemed" | "revoked" | "expired";
  expires_at: string;
  redeemed_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** La respuesta de la EMISIÓN — la única que trae el token en claro. */
interface IssuedInvitation extends Invitation {
  token: string;
}

/**
 * Roles de MEMBERSHIP. `system_admin` no está y no debe estar: es un flag
 * global del usuario, no un rol dentro de un tenant (mismo criterio que la
 * pantalla de Usuarios).
 */
const MEMBERSHIP_ROLES = [
  "tenant_admin",
  "tenant_user",
  "plan_approver",
  "system_operator",
] as const;

const ROLE_KEY: Record<(typeof MEMBERSHIP_ROLES)[number], MessageKey<"invitations">> = {
  tenant_admin: "roleTenantAdmin",
  tenant_user: "roleTenantUser",
  plan_approver: "rolePlanApprover",
  system_operator: "roleSystemOperator",
};

const STATUS_KEY: Record<Invitation["status"], MessageKey<"invitations">> = {
  pending: "statusPending",
  redeemed: "statusRedeemed",
  revoked: "statusRevoked",
  expired: "statusExpired",
};

const STATUS_VARIANT: Record<Invitation["status"], BadgeVariant> = {
  pending: "primary",
  redeemed: "success",
  revoked: "muted",
  expired: "muted",
};

/** Vigencia por defecto que ofrece el formulario: 7 días, como el backend. */
const DEFAULT_TTL_HOURS = 168;

export default function InvitationsPage() {
  const t = useT("invitations");
  return (
    <div className="p-6">
      <PageHeader
        icon={<Ticket className="h-6 w-6 text-white" />}
        title={t("title")}
        description={t("description")}
        data-testid="invitations-header"
      />
      <RoleGuard
        min="system_admin"
        fallback={
          <Card>
            <CardContent className="flex items-center gap-3 py-8">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
            </CardContent>
          </Card>
        }
      >
        <InvitationsContent />
      </RoleGuard>
    </div>
  );
}

function InvitationsContent() {
  const t = useT("invitations");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const [email, setEmail] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [role, setRole] = useState<string>("tenant_user");
  const [ttlHours, setTtlHours] = useState(String(DEFAULT_TTL_HOURS));
  const [issued, setIssued] = useState<IssuedInvitation | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const tenantsQuery = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => apiFetch<Tenant[]>("/admin/tenants"),
    refetchOnWindowFocus: false,
  });

  const invitationsQuery = useQuery({
    queryKey: ["admin-invitations"],
    queryFn: () => apiFetch<Invitation[]>("/admin/invitations"),
    refetchOnWindowFocus: false,
  });

  const issueMutation = useMutation({
    mutationFn: () =>
      apiFetch<IssuedInvitation>("/admin/invitations", {
        method: "POST",
        body: {
          email,
          tenant_id: tenantId,
          role,
          expires_in_hours: Number(ttlHours) || DEFAULT_TTL_HOURS,
        },
      }),
    onSuccess: (created) => {
      setIssued(created);
      setFormError(null);
      setEmail("");
      void queryClient.invalidateQueries({ queryKey: ["admin-invitations"] });
    },
    onError: (err: unknown) => setFormError(errorText(err)),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<Invitation>(`/admin/invitations/${id}/revoke`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-invitations"] });
    },
  });

  const tenants = tenantsQuery.data ?? [];
  const invitations = invitationsQuery.data ?? [];

  // El enlace se compone en el cliente para que apunte al MISMO origen desde el
  // que el admin está trabajando: cablear un dominio aquí produciría enlaces
  // rotos en cuanto la instalación no sea la de desarrollo.
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const inviteLink = issued ? `${origin}/accept-invite?token=${issued.token}` : "";

  return (
    <>
      <Card className="mt-6">
        <CardContent className="py-5">
          <form
            className="grid gap-4 sm:grid-cols-5"
            data-testid="invitation-form"
            onSubmit={(e) => {
              e.preventDefault();
              issueMutation.mutate();
            }}
          >
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="invitation-email">{t("emailLabel")}</Label>
              <Input
                id="invitation-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                data-testid="invitation-email"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invitation-tenant">{t("tenantLabel")}</Label>
              <Select
                id="invitation-tenant"
                required
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                data-testid="invitation-tenant"
              >
                <option value="" />
                {tenants.map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>
                    {tenant.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invitation-role">{t("roleLabel")}</Label>
              <Select
                id="invitation-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                data-testid="invitation-role"
              >
                {MEMBERSHIP_ROLES.map((value) => (
                  <option key={value} value={value}>
                    {t(ROLE_KEY[value])}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invitation-ttl">{t("ttlLabel")}</Label>
              <Input
                id="invitation-ttl"
                type="number"
                min={1}
                max={720}
                value={ttlHours}
                onChange={(e) => setTtlHours(e.target.value)}
                data-testid="invitation-ttl"
              />
            </div>
            <div className="sm:col-span-5">
              {formError && (
                <p
                  className="text-destructive mb-2 text-sm"
                  role="alert"
                  data-testid="invitation-error"
                >
                  {formError}
                </p>
              )}
              <Button
                type="submit"
                disabled={issueMutation.isPending}
                data-testid="invitation-submit"
              >
                <Mail className="mr-2 h-4 w-4" />
                {issueMutation.isPending ? t("issuing") : t("issue")}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="mt-6">
        <StateBlock
          isLoading={invitationsQuery.isLoading}
          isError={invitationsQuery.isError}
          error={invitationsQuery.error}
          isEmpty={invitations.length === 0}
          loadingTestId="invitations-loading"
          errorTestId="invitations-error"
          empty={
            <Card>
              <CardContent className="py-10 text-center">
                <p className="text-muted-foreground text-sm italic" data-testid="invitations-empty">
                  {t("empty")}
                </p>
              </CardContent>
            </Card>
          }
        >
          <div className="rounded-xl border" data-testid="invitations-table">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead className="px-3">{t("colEmail")}</TableHead>
                  <TableHead className="px-3">{t("colTenant")}</TableHead>
                  <TableHead className="px-3">{t("colRole")}</TableHead>
                  <TableHead className="px-3">{t("colCode")}</TableHead>
                  <TableHead className="px-3">{t("colStatus")}</TableHead>
                  <TableHead className="px-3">{t("colExpires")}</TableHead>
                  <TableHead className="px-3 text-right">{t("colActions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations.map((invitation) => (
                  <TableRow key={invitation.id} data-testid={`invitation-row-${invitation.id}`}>
                    <TableCell className="px-3 font-mono text-xs">{invitation.email}</TableCell>
                    <TableCell className="px-3">{invitation.tenant_name ?? "—"}</TableCell>
                    <TableCell className="px-3">
                      {t(
                        ROLE_KEY[invitation.role as (typeof MEMBERSHIP_ROLES)[number]] ?? "colRole",
                      )}
                    </TableCell>
                    {/* Solo el prefijo: el valor completo no existe fuera del
                        momento de la emisión. */}
                    <TableCell className="px-3 font-mono text-xs">
                      {invitation.token_prefix}
                    </TableCell>
                    <TableCell className="px-3">
                      <Badge variant={STATUS_VARIANT[invitation.status]}>
                        {t(STATUS_KEY[invitation.status])}
                      </Badge>
                    </TableCell>
                    <TableCell className="px-3 text-xs">
                      {new Date(invitation.expires_at).toLocaleString()}
                    </TableCell>
                    <TableCell className="px-3 text-right">
                      {/* Una invitación ya canjeada NO se revoca: el usuario
                          existe y lo que hay que quitarle es la membresía, en
                          `/admin/users`. Ofrecer el botón daría la falsa
                          sensación de haber cerrado una puerta que sigue
                          abierta. */}
                      {invitation.status === "pending" || invitation.status === "expired" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => revokeMutation.mutate(invitation.id)}
                          data-testid={`invitation-revoke-${invitation.id}`}
                        >
                          <Trash2 className="mr-1 h-3 w-3" />
                          {t("revoke")}
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </StateBlock>
      </div>

      <Dialog open={issued !== null} onOpenChange={(open) => !open && setIssued(null)}>
        <DialogContent data-testid="issued-dialog">
          <DialogHeader>
            <DialogTitle>{t("tokenOnceTitle")}</DialogTitle>
            <DialogDescription data-testid="issued-token-warning">
              {t("tokenOnceBody")}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="issued-token">{t("colCode")}</Label>
              <Input
                id="issued-token"
                readOnly
                value={issued?.token ?? ""}
                data-testid="issued-token"
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="issued-link">{t("linkLabel")}</Label>
              <Input
                id="issued-link"
                readOnly
                value={inviteLink}
                data-testid="issued-link"
                className="font-mono text-xs"
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => setIssued(null)} data-testid="issued-close">
              {tCommon("close")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
