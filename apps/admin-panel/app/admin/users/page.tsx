"use client";

/**
 * task_sso_04 — Pantalla 'Usuarios' del System Admin (ADR 0047).
 *
 * Los usuarios son **globales** (la tabla `users` no tiene `tenant_id`); el
 * acceso a cada tenant lo dan EXCLUSIVAMENTE las `UserOrganizationMembership`
 * que asigna el System Admin aquí (sin claiming por dominio, sin alta
 * automática — ADR 0047, deny-by-default). Esta pantalla deja al System
 * Admin:
 *
 *   - Listar los usuarios de la plataforma (email / nombre / system_admin /
 *     activo) con búsqueda por email/nombre.
 *   - Por usuario, gestionar sus memberships (tenant + rol): asignar un
 *     tenant con rol, cambiar el rol, activar/desactivar y revocar.
 *
 * Toda la pantalla es System Admin: el backend gatea cada endpoint con
 * `require_system_admin` (motor BYPASSRLS) y `<RoleGuard min="system_admin">`
 * evita mostrar la superficie a otros roles (la barrera real es el backend).
 *
 * Endpoints backend:
 *   GET    /admin/users                                  — lista global de usuarios
 *   GET    /admin/tenants                                — tenants para el selector
 *   GET    /admin/users/{id}/memberships                 — memberships del usuario
 *   POST   /admin/users/{id}/memberships                 — asignar tenant + rol (201)
 *   PATCH  /admin/users/{id}/memberships/{membershipId}  — cambiar rol / activar-desactivar
 *   DELETE /admin/users/{id}/memberships/{membershipId}  — revocar (soft-delete)
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Check,
  Plus,
  Search,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Users,
  X,
} from "lucide-react";

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
import { ApiError, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.admin.{UserListItem,MembershipResponse}.
// ---------------------------------------------------------------------------
interface AdminUser {
  id: string;
  email: string;
  full_name: string | null;
  is_system_admin: boolean;
  is_active: boolean;
}

interface TenantSummary {
  id: string;
  name: string;
  slug: string;
}

interface Membership {
  id: string;
  user_id: string;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// Per-membership roles, mirroring db.models.UserRole (system_admin is a
// global flag on the user, never a membership role).
type MembershipRole = "tenant_admin" | "tenant_user" | "system_operator";

const ROLES: MembershipRole[] = ["tenant_admin", "tenant_user", "system_operator"];

const ROLE_LABEL: Record<MembershipRole, string> = {
  tenant_admin: "Tenant Admin",
  tenant_user: "Tenant User",
  system_operator: "System Operator",
};

const ROLE_BADGE: Record<string, BadgeVariant> = {
  tenant_admin: "primary",
  tenant_user: "info",
  system_operator: "warning",
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

// ===========================================================================
// Page
// ===========================================================================
export default function UsersPage() {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="users-page">
      <PageHeader
        icon={<Users className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Usuarios"
        description="Usuarios globales de la plataforma. El acceso a cada tenant lo dan las membership (usuario↔tenant + rol) que asigna el System Admin (ADR 0047)."
        data-testid="users-header"
      />
      {/* La pantalla completa es solo System Admin: el backend gatea con
          require_system_admin sobre la sesión BYPASSRLS. */}
      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6" data-testid="users-forbidden">
            <CardContent className="flex items-center gap-3 py-10">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">
                Esta sección es exclusiva del System Admin de la plataforma.
              </p>
            </CardContent>
          </Card>
        }
      >
        <UsersContent />
      </RoleGuard>
    </div>
  );
}

function UsersContent() {
  const [query, setQuery] = useState("");
  const [membershipsTarget, setMembershipsTarget] = useState<AdminUser | null>(null);

  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => apiFetch<AdminUser[]>("/admin/users"),
    refetchOnWindowFocus: false,
  });

  const users = usersQuery.data ?? [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return users;
    return users.filter(
      (u) => u.email.toLowerCase().includes(q) || (u.full_name ?? "").toLowerCase().includes(q),
    );
  }, [users, query]);

  return (
    <>
      <div className="mt-6">
        <div className="relative max-w-sm">
          <Search className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar por email o nombre…"
            className="pl-9"
            data-testid="users-search"
            aria-label="Buscar usuarios"
          />
        </div>
      </div>

      <div className="mt-4">
        <StateBlock
          isLoading={usersQuery.isLoading}
          isError={usersQuery.isError}
          error={usersQuery.error}
          isEmpty={filtered.length === 0}
          loadingLabel="Cargando usuarios…"
          loadingTestId="users-loading"
          errorTestId="users-error"
          empty={
            <Card>
              <CardContent className="py-10 text-center">
                <p className="text-muted-foreground text-sm italic" data-testid="users-empty">
                  {query.trim() === ""
                    ? "No hay usuarios en la plataforma."
                    : "Ningún usuario coincide con la búsqueda."}
                </p>
              </CardContent>
            </Card>
          }
        >
          <div className="rounded-xl border" data-testid="users-table">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead className="px-3">Usuario</TableHead>
                  <TableHead className="px-3">Email</TableHead>
                  <TableHead className="px-3">Tipo</TableHead>
                  <TableHead className="px-3">Estado</TableHead>
                  <TableHead className="px-3 text-right">Acceso a tenants</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((u) => (
                  <TableRow key={u.id} data-testid={`user-row-${u.id}`}>
                    <TableCell className="px-3 font-medium">{u.full_name ?? "—"}</TableCell>
                    <TableCell className="px-3 font-mono text-xs">{u.email}</TableCell>
                    <TableCell className="px-3">
                      {u.is_system_admin ? (
                        <Badge variant="primary">
                          <ShieldCheck className="mr-1 h-3 w-3" />
                          System Admin
                        </Badge>
                      ) : (
                        <Badge variant="muted">Usuario</Badge>
                      )}
                    </TableCell>
                    <TableCell className="px-3">
                      {u.is_active ? (
                        <Badge variant="success">activo</Badge>
                      ) : (
                        <Badge variant="muted">inactivo</Badge>
                      )}
                    </TableCell>
                    <TableCell className="px-3">
                      <div className="flex items-center justify-end">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setMembershipsTarget(u)}
                          data-testid={`user-memberships-open-${u.id}`}
                        >
                          <Building2 className="mr-1 h-3.5 w-3.5" />
                          Gestionar tenants
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </StateBlock>
      </div>

      {membershipsTarget ? (
        <MembershipsDialog user={membershipsTarget} onClose={() => setMembershipsTarget(null)} />
      ) : null}
    </>
  );
}

// ===========================================================================
// Memberships dialog — per user: list, assign, set role, activate, revoke.
// ===========================================================================
interface MembershipsDialogProps {
  user: AdminUser;
  onClose: () => void;
}

function MembershipsDialog({ user, onClose }: MembershipsDialogProps) {
  const queryClient = useQueryClient();
  const membershipsKey = ["admin-user-memberships", user.id];

  const membershipsQuery = useQuery({
    queryKey: membershipsKey,
    queryFn: () => apiFetch<Membership[]>(`/admin/users/${user.id}/memberships`),
    refetchOnWindowFocus: false,
  });

  const tenantsQuery = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => apiFetch<TenantSummary[]>("/admin/tenants"),
    refetchOnWindowFocus: false,
  });

  const memberships = membershipsQuery.data ?? [];
  const tenants = tenantsQuery.data ?? [];

  // Tenants the user is NOT already a member of (the assign form's options).
  const assignedTenantIds = new Set(memberships.map((m) => m.tenant_id));
  const availableTenants = tenants.filter((t) => !assignedTenantIds.has(t.id));

  const [assignTenantId, setAssignTenantId] = useState("");
  const [assignRole, setAssignRole] = useState<MembershipRole>("tenant_user");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: membershipsKey });
  };

  const assignMutation = useMutation({
    mutationFn: () =>
      apiFetch<Membership>(`/admin/users/${user.id}/memberships`, {
        method: "POST",
        body: { tenant_id: assignTenantId, role: assignRole },
      }),
    onSuccess: () => {
      setAssignTenantId("");
      setAssignRole("tenant_user");
      invalidate();
    },
  });

  const updateMutation = useMutation({
    mutationFn: (vars: { membershipId: string; body: Record<string, unknown> }) =>
      apiFetch<Membership>(`/admin/users/${user.id}/memberships/${vars.membershipId}`, {
        method: "PATCH",
        body: vars.body,
      }),
    onSuccess: invalidate,
  });

  const revokeMutation = useMutation({
    mutationFn: (membershipId: string) =>
      apiFetch<void>(`/admin/users/${user.id}/memberships/${membershipId}`, {
        method: "DELETE",
      }),
    onSuccess: invalidate,
  });

  const canAssign = assignTenantId !== "" && !assignMutation.isPending;

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogContent data-testid="memberships-dialog">
        <DialogHeader>
          <DialogTitle>Acceso a tenants — {user.full_name ?? user.email}</DialogTitle>
          <DialogDescription>
            El acceso a cada tenant lo da una membership con un rol. Sin membership activa, el
            usuario no entra a ningún tenant (ADR 0047).
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {/* Existing memberships ------------------------------------------------ */}
          <StateBlock
            isLoading={membershipsQuery.isLoading}
            isError={membershipsQuery.isError}
            error={membershipsQuery.error}
            isEmpty={memberships.length === 0}
            loadingLabel="Cargando membership…"
            loadingTestId="memberships-loading"
            errorTestId="memberships-error"
            empty={
              <p
                className="text-muted-foreground rounded-lg border border-dashed p-4 text-center text-sm italic"
                data-testid="memberships-empty"
              >
                El usuario no tiene acceso a ningún tenant. Asígnale uno abajo.
              </p>
            }
          >
            <div className="rounded-xl border" data-testid="memberships-table">
              <Table>
                <TableHeader className="bg-muted">
                  <TableRow>
                    <TableHead className="px-3">Tenant</TableHead>
                    <TableHead className="px-3">Rol</TableHead>
                    <TableHead className="px-3">Estado</TableHead>
                    <TableHead className="px-3 text-right">Acciones</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {memberships.map((m) => (
                    <TableRow key={m.id} data-testid={`membership-row-${m.id}`}>
                      <TableCell className="px-3">
                        <span className="flex items-center gap-2">
                          <Building2 className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
                          <span className="font-medium">{m.tenant_name}</span>
                          <span className="text-muted-foreground font-mono text-xs">
                            {m.tenant_slug}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="px-3">
                        <Select
                          value={m.role}
                          onChange={(e) =>
                            updateMutation.mutate({
                              membershipId: m.id,
                              body: { role: e.target.value },
                            })
                          }
                          disabled={updateMutation.isPending}
                          className="h-8 w-auto text-xs"
                          data-testid={`membership-role-${m.id}`}
                          aria-label={`Rol de ${m.tenant_name}`}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {ROLE_LABEL[r]}
                            </option>
                          ))}
                        </Select>
                      </TableCell>
                      <TableCell className="px-3">
                        <button
                          type="button"
                          onClick={() =>
                            updateMutation.mutate({
                              membershipId: m.id,
                              body: { is_active: !m.is_active },
                            })
                          }
                          disabled={updateMutation.isPending}
                          className="focus-visible:ring-ring focus-visible:ring-offset-background cursor-pointer rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid={`membership-toggle-${m.id}`}
                          aria-label={m.is_active ? "Desactivar membership" : "Activar membership"}
                          aria-pressed={m.is_active}
                        >
                          {m.is_active ? (
                            <Badge variant={ROLE_BADGE[m.role] ?? "success"}>
                              <Check className="mr-1 h-3 w-3" />
                              activa
                            </Badge>
                          ) : (
                            <Badge variant="muted">
                              <X className="mr-1 h-3 w-3" />
                              inactiva
                            </Badge>
                          )}
                        </button>
                      </TableCell>
                      <TableCell className="px-3">
                        <div className="flex items-center justify-end">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => revokeMutation.mutate(m.id)}
                            disabled={revokeMutation.isPending}
                            data-testid={`membership-revoke-${m.id}`}
                            aria-label="Revocar acceso"
                          >
                            <Trash2 className="text-destructive h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </StateBlock>

          {(updateMutation.isError || revokeMutation.isError) && (
            <p className="text-destructive mt-3 text-xs" data-testid="memberships-mutation-error">
              {errorText(updateMutation.error ?? revokeMutation.error)}
            </p>
          )}

          {/* Assign form --------------------------------------------------------- */}
          <div className="mt-6 rounded-xl border p-4" data-testid="membership-assign">
            <Label className="text-sm font-semibold">Asignar acceso a un tenant</Label>
            <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="flex-1 space-y-1">
                <Label htmlFor="assign-tenant" className="text-xs">
                  Tenant
                </Label>
                <Select
                  id="assign-tenant"
                  value={assignTenantId}
                  onChange={(e) => setAssignTenantId(e.target.value)}
                  disabled={availableTenants.length === 0}
                  data-testid="assign-tenant"
                >
                  <option value="">
                    {availableTenants.length === 0
                      ? "Sin tenants disponibles"
                      : "Selecciona un tenant…"}
                  </option>
                  {availableTenants.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.slug})
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1 sm:w-48">
                <Label htmlFor="assign-role" className="text-xs">
                  Rol
                </Label>
                <Select
                  id="assign-role"
                  value={assignRole}
                  onChange={(e) => setAssignRole(e.target.value as MembershipRole)}
                  data-testid="assign-role"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </Select>
              </div>
              <Button
                onClick={() => assignMutation.mutate()}
                disabled={!canAssign}
                data-testid="assign-submit"
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                {assignMutation.isPending ? "Asignando…" : "Asignar"}
              </Button>
            </div>
            {availableTenants.length === 0 && tenants.length > 0 ? (
              <p className="text-muted-foreground mt-2 text-xs">
                El usuario ya tiene acceso a todos los tenants existentes.
              </p>
            ) : null}
            {assignMutation.isError ? (
              <p className="text-destructive mt-2 text-xs" data-testid="assign-error">
                {errorText(assignMutation.error)}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="memberships-close">
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
