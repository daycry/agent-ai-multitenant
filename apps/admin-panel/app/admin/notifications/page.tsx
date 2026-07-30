"use client";

/**
 * Plan 10 task_10_15 — UI de configuración de notificaciones en 3 capas.
 *
 * Tres pestañas que mapean los tres scopes del modelo (CLAUDE.md §6,
 * plataforma → tenant → usuario):
 *
 *   - **Plataforma** (solo System Admin): qué transportes (Telegram, Email,
 *     Slack, …) están habilitados globalmente. Un tenant solo puede
 *     configurar canales de un transporte habilitado aquí.
 *   - **Canales** (Tenant Admin): canales concretos del tenant / del propio
 *     admin (scope tenant|user), con su secreto. El secreto se cifra en
 *     reposo en el backend y NUNCA se devuelve: la UI solo sabe si hay
 *     secreto (`has_secret` + `secret_source`).
 *   - **Preferencias** (Tenant Admin): reglas de enrutado evento→canal
 *     (opt-in/out, horas de silencio) — el primitivo del test human_10_02.
 *
 * Permisos: el backend es la fuente de verdad (RBAC por scope + RLS). La UI
 * envuelve las acciones de escritura en <RoleGuard> y oculta la pestaña de
 * plataforma a quien no sea System Admin, pero nunca confía solo en eso.
 *
 * Endpoints (routers/notifications.py):
 *   GET    /notifications/platform/channel-types   (lectura: cualquier miembro)
 *   PUT    /notifications/platform/channel-types   (System Admin)
 *   GET    /notifications/channels
 *   POST   /notifications/channels
 *   PUT    /notifications/channels/{id}
 *   DELETE /notifications/channels/{id}
 *   GET    /notifications/preferences
 *   PUT    /notifications/preferences
 *   DELETE /notifications/preferences/{id}
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiFetch } from "@/lib/api";
import { useCurrentUser } from "@/lib/use-current-user";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.notifications
// --------------------------------------------------------------------------
type ChannelScope = "tenant" | "user";
type SecretSource = "vault" | "encrypted";

interface PlatformChannelTypes {
  enabled: string[];
  available: string[];
}

interface NotificationChannel {
  id: string;
  scope: string;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  owner_user_id: string | null;
  has_secret: boolean;
  secret_source: SecretSource | null;
  created_at: string;
  updated_at: string;
}

interface NotificationPreference {
  id: string;
  scope: string;
  event_type: string;
  channel_type: string;
  enabled: boolean;
  owner_user_id: string | null;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
  quiet_hours_tz: string | null;
}

interface ChannelCreateBody {
  scope: ChannelScope;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  secret?: string;
}

interface ChannelUpdateBody {
  name?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  secret?: string;
}

interface PreferenceUpsertBody {
  scope: ChannelScope;
  event_type: string;
  channel_type: string;
  enabled: boolean;
}

const SECRET_SOURCE_LABEL: Record<SecretSource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// NOTIF-3: el catálogo de eventos se sirve desde el backend
// (GET /notifications/event-catalog, en sync con el EVENT_REGISTRY real del
// dispatcher vía test). El hardcode anterior ofrecía 4 eventos, uno inexistente
// (review_needed). Fallback mínimo por si el endpoint falla.
interface EventCatalogEntry {
  event_type: string;
  label_es: string;
  label_en: string;
}
const EVENT_CATALOG_FALLBACK: EventCatalogEntry[] = [
  { event_type: "task_blocked", label_es: "Tarea bloqueada", label_en: "Task blocked" },
  { event_type: "budget_alert", label_es: "Alerta de presupuesto", label_en: "Budget alert" },
];

function apiErrorBody(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function NotificationConfigPage() {
  const { isSystemAdmin } = useCurrentUser();

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="notification-config-page"
    >
      <PageHeader
        icon={<Bell className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Notificaciones"
        description="Configuración de canales y preferencias en 3 capas: plataforma, tenant y usuario."
        data-testid="notification-config-header"
      />

      <Tabs defaultValue="channels" className="mt-6">
        <TabsList data-testid="notification-tabs">
          <TabsTrigger value="channels" data-testid="tab-channels">
            Canales
          </TabsTrigger>
          <TabsTrigger value="preferences" data-testid="tab-preferences">
            Preferencias
          </TabsTrigger>
          {isSystemAdmin ? (
            <TabsTrigger value="platform" data-testid="tab-platform">
              Plataforma
            </TabsTrigger>
          ) : null}
        </TabsList>

        <TabsContent value="channels">
          <ChannelsTab />
        </TabsContent>
        <TabsContent value="preferences">
          <PreferencesTab />
        </TabsContent>
        {isSystemAdmin ? (
          <TabsContent value="platform">
            <PlatformTab />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  );
}

// ==========================================================================
// Platform tab — System Admin enables transports globally
// ==========================================================================
function PlatformTab() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["notification-platform-types"],
    queryFn: () => apiFetch<PlatformChannelTypes>("/notifications/platform/channel-types"),
    refetchOnWindowFocus: false,
  });

  const [draft, setDraft] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (query.data) setDraft(new Set(query.data.enabled));
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: (enabled: string[]) =>
      apiFetch<PlatformChannelTypes>("/notifications/platform/channel-types", {
        method: "PUT",
        body: { enabled },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-platform-types"] }),
  });

  if (query.isLoading) {
    return <p className="text-muted-foreground mt-4 text-sm">Cargando…</p>;
  }
  if (query.isError || !query.data || draft === null) {
    return (
      <p className="text-destructive mt-4 text-sm" data-testid="platform-error">
        {apiErrorBody(query.error)}
      </p>
    );
  }

  function toggle(type: string) {
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <Card className="mt-4" data-testid="platform-channel-types">
      <CardHeader>
        <CardTitle className="text-base">Transportes habilitados globalmente</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground mb-4 text-sm">
          Un tenant solo puede configurar canales de los transportes habilitados aquí.
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {query.data.available.map((type) => (
            <label
              key={type}
              className="flex items-center gap-2 text-sm capitalize"
              htmlFor={`platform-type-${type}`}
            >
              <input
                id={`platform-type-${type}`}
                data-testid={`platform-type-${type}`}
                type="checkbox"
                className="h-4 w-4 rounded border"
                checked={draft.has(type)}
                onChange={() => toggle(type)}
              />
              <span>{type}</span>
            </label>
          ))}
        </div>
        <RoleGuard min="system_admin">
          <div className="mt-6 flex items-center gap-3">
            <Button
              onClick={() => saveMutation.mutate([...draft])}
              disabled={saveMutation.isPending}
              data-testid="platform-save"
            >
              {saveMutation.isPending ? "Guardando…" : "Guardar"}
            </Button>
            {saveMutation.isError ? (
              <span className="text-destructive text-xs" data-testid="platform-save-error">
                {apiErrorBody(saveMutation.error)}
              </span>
            ) : null}
          </div>
        </RoleGuard>
      </CardContent>
    </Card>
  );
}

// ==========================================================================
// Channels tab — tenant / user scoped CRUD
// ==========================================================================
function ChannelsTab() {
  const queryClient = useQueryClient();
  const channelsQuery = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => apiFetch<NotificationChannel[]>("/notifications/channels"),
    refetchOnWindowFocus: false,
  });
  const typesQuery = useQuery({
    queryKey: ["notification-platform-types"],
    queryFn: () => apiFetch<PlatformChannelTypes>("/notifications/platform/channel-types"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);

  const saveMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string | null;
      body: ChannelCreateBody | ChannelUpdateBody;
    }) =>
      id === null
        ? apiFetch<NotificationChannel>("/notifications/channels", { method: "POST", body })
        : apiFetch<NotificationChannel>(`/notifications/channels/${id}`, {
            method: "PUT",
            body,
          }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notification-channels"] });
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(`/notifications/channels/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-channels"] }),
  });

  const enabledTypes = typesQuery.data?.enabled ?? [];

  function handleCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function handleEdit(channel: NotificationChannel) {
    setEditing(channel);
    setDialogOpen(true);
  }

  function handleDelete(channel: NotificationChannel) {
    if (!window.confirm(`¿Eliminar el canal “${channel.name}”?`)) return;
    deleteMutation.mutate(channel.id);
  }

  return (
    <div className="mt-4" data-testid="channels-tab">
      <div className="mb-4 flex justify-end">
        <RoleGuard min="tenant_admin">
          <Button onClick={handleCreate} data-testid="channel-create-button">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Nuevo canal
          </Button>
        </RoleGuard>
      </div>

      {channelsQuery.isLoading ? (
        <p className="text-muted-foreground text-sm" data-testid="channels-loading">
          Cargando…
        </p>
      ) : channelsQuery.isError ? (
        <p className="text-destructive text-sm" data-testid="channels-error">
          {apiErrorBody(channelsQuery.error)}
        </p>
      ) : (channelsQuery.data ?? []).length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="channels-empty">
              Aún no hay canales configurados. Pulsa <strong>“Nuevo canal”</strong> para añadir uno.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3" data-testid="channels-list">
          {(channelsQuery.data ?? []).map((channel) => (
            <ChannelCard
              key={channel.id}
              channel={channel}
              onEdit={() => handleEdit(channel)}
              onDelete={() => handleDelete(channel)}
              busy={deleteMutation.isPending}
            />
          ))}
        </div>
      )}

      {dialogOpen ? (
        <ChannelDialog
          open={dialogOpen}
          onOpenChange={(next) => {
            if (!saveMutation.isPending) setDialogOpen(next);
          }}
          initial={editing}
          enabledTypes={enabledTypes}
          submitting={saveMutation.isPending}
          onSubmit={(body) => saveMutation.mutate({ id: editing?.id ?? null, body })}
          backendError={saveMutation.isError ? apiErrorBody(saveMutation.error) : null}
        />
      ) : null}
    </div>
  );
}

function ChannelCard({
  channel,
  onEdit,
  onDelete,
  busy,
}: {
  channel: NotificationChannel;
  onEdit: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  return (
    <Card data-testid={`channel-card-${channel.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <span className="truncate">{channel.name}</span>
            <Badge variant="info" data-testid={`channel-type-${channel.id}`}>
              {channel.channel_type}
            </Badge>
            <Badge variant="muted">{channel.scope}</Badge>
            <Badge variant={channel.enabled ? "success" : "muted"}>
              {channel.enabled ? "activo" : "inactivo"}
            </Badge>
            {channel.has_secret ? (
              <Badge variant="info" data-testid={`channel-secret-${channel.id}`}>
                <KeyRound className="mr-1 h-3 w-3" />
                secreto: {SECRET_SOURCE_LABEL[channel.secret_source ?? "encrypted"]}
              </Badge>
            ) : (
              <Badge variant="warning">sin secreto</Badge>
            )}
          </CardTitle>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <RoleGuard min="tenant_admin">
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={busy}
              data-testid={`channel-edit-${channel.id}`}
              aria-label="Editar canal"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={busy}
              data-testid={`channel-delete-${channel.id}`}
              aria-label="Eliminar canal"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </RoleGuard>
        </div>
      </CardHeader>
    </Card>
  );
}

interface ChannelFormState {
  scope: ChannelScope;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: string;
  secret: string;
}

function channelToForm(
  channel: NotificationChannel | null,
  enabledTypes: string[],
): ChannelFormState {
  if (channel === null) {
    return {
      scope: "tenant",
      channel_type: enabledTypes[0] ?? "telegram",
      name: "",
      enabled: true,
      config: "{}",
      secret: "",
    };
  }
  return {
    scope: (channel.scope as ChannelScope) ?? "tenant",
    channel_type: channel.channel_type,
    name: channel.name,
    enabled: channel.enabled,
    config: JSON.stringify(channel.config ?? {}, null, 2),
    secret: "",
  };
}

function ChannelDialog({
  open,
  onOpenChange,
  initial,
  enabledTypes,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: NotificationChannel | null;
  enabledTypes: string[];
  submitting: boolean;
  onSubmit: (body: ChannelCreateBody | ChannelUpdateBody) => void;
  backendError: string | null;
}) {
  const isCreate = initial === null;
  const [state, setState] = useState<ChannelFormState>(() => channelToForm(initial, enabledTypes));
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    setState(channelToForm(initial, enabledTypes));
    setConfigError(null);
  }, [initial, enabledTypes]);

  function buildBody(): ChannelCreateBody | ChannelUpdateBody | null {
    let parsedConfig: Record<string, unknown> = {};
    try {
      parsedConfig = state.config.trim() === "" ? {} : JSON.parse(state.config);
    } catch {
      setConfigError("El config no es un JSON válido.");
      return null;
    }
    if (isCreate) {
      const body: ChannelCreateBody = {
        scope: state.scope,
        channel_type: state.channel_type,
        name: state.name.trim(),
        enabled: state.enabled,
        config: parsedConfig,
      };
      if (state.secret.trim()) body.secret = state.secret;
      return body;
    }
    const body: ChannelUpdateBody = {
      name: state.name.trim(),
      enabled: state.enabled,
      config: parsedConfig,
    };
    if (state.secret.trim()) body.secret = state.secret;
    return body;
  }

  const canSubmit = state.name.trim() !== "" && state.channel_type !== "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="channel-dialog">
        <DialogHeader>
          <DialogTitle>{isCreate ? "Nuevo canal" : "Editar canal"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {isCreate ? (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label htmlFor="channel-form-scope">Ámbito</Label>
                  <select
                    id="channel-form-scope"
                    data-testid="channel-form-scope"
                    className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                    value={state.scope}
                    onChange={(e) => setState({ ...state, scope: e.target.value as ChannelScope })}
                  >
                    <option value="tenant">Tenant (compartido)</option>
                    <option value="user">Usuario (solo yo)</option>
                  </select>
                </div>
                <div>
                  <Label htmlFor="channel-form-type">Transporte</Label>
                  <select
                    id="channel-form-type"
                    data-testid="channel-form-type"
                    className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                    value={state.channel_type}
                    onChange={(e) => setState({ ...state, channel_type: e.target.value })}
                  >
                    {enabledTypes.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ) : null}

            <div>
              <Label htmlFor="channel-form-name">Nombre</Label>
              <Input
                id="channel-form-name"
                data-testid="channel-form-name"
                value={state.name}
                onChange={(e) => setState({ ...state, name: e.target.value })}
                placeholder="Ops bot"
              />
            </div>

            <div>
              <Label htmlFor="channel-form-config">Config (JSON, sin secretos)</Label>
              <textarea
                id="channel-form-config"
                data-testid="channel-form-config"
                className="border-input bg-background mt-1 min-h-[96px] w-full rounded-md border px-3 py-2 font-mono text-xs"
                value={state.config}
                onChange={(e) => setState({ ...state, config: e.target.value })}
                placeholder='{ "chat_id": "12345" }'
              />
              {configError ? (
                <p
                  className="text-destructive mt-1 text-xs"
                  data-testid="channel-form-config-error"
                >
                  {configError}
                </p>
              ) : null}
            </div>

            <div>
              <Label htmlFor="channel-form-secret">
                Secreto{isCreate ? " (opcional)" : " (dejar vacío para conservar el actual)"}
              </Label>
              <Input
                id="channel-form-secret"
                data-testid="channel-form-secret"
                type="password"
                autoComplete="new-password"
                value={state.secret}
                onChange={(e) => setState({ ...state, secret: e.target.value })}
                placeholder={isCreate ? "token del bot / contraseña / clave" : "••••••••"}
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Se cifra en reposo antes de guardarse; el sistema nunca lo devuelve en claro.
              </p>
            </div>

            <label className="flex items-center gap-2 text-sm" htmlFor="channel-form-enabled">
              <input
                id="channel-form-enabled"
                data-testid="channel-form-enabled"
                type="checkbox"
                className="h-4 w-4 rounded border"
                checked={state.enabled}
                onChange={(e) => setState({ ...state, enabled: e.target.checked })}
              />
              <span>Canal activo</span>
            </label>

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="channel-form-backend-error"
              >
                {backendError}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid="channel-form-cancel"
          >
            Cancelar
          </Button>
          <Button
            onClick={() => {
              const body = buildBody();
              if (body !== null) onSubmit(body);
            }}
            disabled={submitting || !canSubmit}
            data-testid="channel-form-submit"
          >
            {submitting ? "Guardando…" : isCreate ? "Crear" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ==========================================================================
// Preferences tab — routing rules (event x channel -> opt in/out)
// ==========================================================================
function PreferencesTab() {
  const queryClient = useQueryClient();
  const prefsQuery = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => apiFetch<NotificationPreference[]>("/notifications/preferences"),
    refetchOnWindowFocus: false,
  });
  const channelsQuery = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => apiFetch<NotificationChannel[]>("/notifications/channels"),
    refetchOnWindowFocus: false,
  });
  const catalogQuery = useQuery({
    queryKey: ["notification-event-catalog"],
    queryFn: () => apiFetch<EventCatalogEntry[]>("/notifications/event-catalog"),
    refetchOnWindowFocus: false,
  });
  const eventCatalog =
    catalogQuery.data && catalogQuery.data.length > 0 ? catalogQuery.data : EVENT_CATALOG_FALLBACK;

  const upsertMutation = useMutation({
    mutationFn: (body: PreferenceUpsertBody) =>
      apiFetch<NotificationPreference>("/notifications/preferences", { method: "PUT", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });

  // The channel transports the tenant actually has configured — the matrix
  // only offers a column per configured transport.
  const channelTypes = useMemo(() => {
    const set = new Set<string>();
    for (const c of channelsQuery.data ?? []) set.add(c.channel_type);
    return [...set].sort();
  }, [channelsQuery.data]);

  // Effective opt-in/out per (event, channel): a stored rule wins, else
  // default ON (the dispatcher's most-specific-wins default).
  const byKey = useMemo(() => {
    const map = new Map<string, NotificationPreference>();
    for (const p of prefsQuery.data ?? []) {
      map.set(`${p.event_type}::${p.channel_type}`, p);
    }
    return map;
  }, [prefsQuery.data]);

  if (prefsQuery.isLoading || channelsQuery.isLoading) {
    return <p className="text-muted-foreground mt-4 text-sm">Cargando…</p>;
  }
  if (prefsQuery.isError) {
    return (
      <p className="text-destructive mt-4 text-sm" data-testid="preferences-error">
        {apiErrorBody(prefsQuery.error)}
      </p>
    );
  }

  return (
    <Card className="mt-4" data-testid="preferences-tab">
      <CardHeader>
        <CardTitle className="text-base">Reglas de enrutado</CardTitle>
      </CardHeader>
      <CardContent>
        {channelTypes.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="preferences-empty">
            Configura al menos un canal para ajustar qué eventos llegan por qué transporte.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="preferences-matrix">
              <thead>
                <tr className="text-muted-foreground text-left">
                  <th className="py-2 pr-4 font-medium">Evento</th>
                  {channelTypes.map((type) => (
                    <th key={type} className="px-3 py-2 font-medium capitalize">
                      {type}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {eventCatalog.map(({ event_type: event, label_es }) => (
                  <tr key={event} className="border-t" data-testid={`preferences-row-${event}`}>
                    <td className="py-2 pr-4">
                      <span className="block text-xs">{label_es}</span>
                      <span className="text-muted-foreground block font-mono text-[10px]">
                        {event}
                      </span>
                    </td>
                    {channelTypes.map((type) => {
                      const rule = byKey.get(`${event}::${type}`);
                      const enabled = rule?.enabled ?? true;
                      return (
                        <td key={type} className="px-3 py-2">
                          <RoleGuard
                            min="tenant_admin"
                            fallback={
                              <span className="text-muted-foreground text-xs">
                                {enabled ? "sí" : "no"}
                              </span>
                            }
                          >
                            <input
                              type="checkbox"
                              className="h-4 w-4 rounded border"
                              checked={enabled}
                              disabled={upsertMutation.isPending}
                              data-testid={`preference-${event}-${type}`}
                              onChange={(e) =>
                                upsertMutation.mutate({
                                  scope: "user",
                                  event_type: event,
                                  channel_type: type,
                                  enabled: e.target.checked,
                                })
                              }
                            />
                          </RoleGuard>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {upsertMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="preferences-save-error">
            {apiErrorBody(upsertMutation.error)}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
