"use client";

/**
 * Pestaña «Canales» — CRUD de canales del tenant o del propio usuario
 * (prod-16 `task_prod16_08`, extracción verbatim del `page.tsx` de 831 líneas).
 *
 * El secreto se cifra en reposo en el backend y NUNCA vuelve: la UI solo sabe
 * si existe (`has_secret` + `secret_source`). Por eso el campo del formulario
 * arranca vacío también al editar, y vacío significa «conserva el actual».
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, Trash2 } from "lucide-react";

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
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

import {
  channelToForm,
  SECRET_SOURCE_LABEL,
  type ChannelCreateBody,
  type ChannelFormState,
  type ChannelScope,
  type ChannelUpdateBody,
  type NotificationChannel,
  type PlatformChannelTypes,
} from "./notification-types";

export function ChannelsTab() {
  const errorText = useErrorText();
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
          {errorText(channelsQuery.error)}
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
          backendError={saveMutation.isError ? errorText(saveMutation.error) : null}
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
