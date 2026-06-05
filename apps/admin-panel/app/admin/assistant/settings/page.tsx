"use client";

/**
 * Asistente personal — ajustes de IDENTIDAD (Plan 10 task assistant-ui).
 *
 * El objetivo principal del operador: personalizar el NOMBRE del asistente,
 * junto con avatar, tono, idioma (es/en), un override del system prompt y la
 * lista de herramientas de solo lectura que el asistente puede usar.
 *
 * Arriba del todo hay un toggle "Asistente habilitado" que un Tenant Admin
 * usa para encender/apagar el asistente de SU tenant (GET/PUT
 * /tenant-settings/personal-assistant — gated SÓLO por tenant_admin, así que
 * sí se puede encender). Cuando está apagado, el resto del formulario de
 * identidad se muestra deshabilitado con un texto claro.
 *
 * La identidad se carga con GET /assistant/identity y se guarda con PUT
 * /assistant/identity (TanStack Query). Esos endpoints SIGUEN gated por el
 * toggle: el BACKEND devuelve 403 si el usuario no es admin o el toggle está
 * apagado. La UI sólo lo REFLEJA — el gate real vive en el servidor.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, MessageSquare } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import {
  ASSISTANT_LIMITS,
  ASSISTANT_TOOL_CATALOGUE,
  getAssistantEnabled,
  identityToFormValues,
  setAssistantEnabled,
  toIdentityUpdate,
  validateAssistantIdentity,
  type AssistantIdentity,
  type AssistantIdentityFormErrors,
  type AssistantIdentityFormValues,
  type AssistantToggleState,
} from "@/lib/assistant";
import { useCurrentUser } from "@/lib/use-current-user";

const EMPTY_VALUES: AssistantIdentityFormValues = {
  name: "",
  avatarUrl: "",
  tone: "",
  language: "es",
  systemPrompt: "",
  enabledTools: [],
};

export default function AssistantSettingsPage() {
  const { isTenantAdmin, isLoading: userLoading } = useCurrentUser();
  const queryClient = useQueryClient();

  const [values, setValues] = useState<AssistantIdentityFormValues>(EMPTY_VALUES);
  const [seeded, setSeeded] = useState(false);
  const [touched, setTouched] = useState(false);

  // The on/off toggle. Gated SOLELY by tenant_admin server-side, so a
  // Tenant Admin can always read it (and flip it) regardless of the toggle
  // state itself — this is what lets the feature be turned on at all.
  const toggleQuery = useQuery<AssistantToggleState, ApiError>({
    queryKey: ["assistant-enabled"],
    queryFn: getAssistantEnabled,
    enabled: isTenantAdmin,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const assistantEnabled = toggleQuery.data?.enabled ?? false;

  const toggleMutation = useMutation<AssistantToggleState, ApiError, boolean>({
    mutationFn: (next) => setAssistantEnabled(next),
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant-enabled"], data);
      // Turning it on/off changes the assistant gate; refresh identity.
      void queryClient.invalidateQueries({ queryKey: ["assistant-identity"] });
    },
  });

  // The identity GET is itself toggle-gated server-side (403 when off), so we
  // only attempt it once we know the toggle is ON and the user is admin —
  // otherwise we'd just provoke a 403 we already expect.
  const identityQuery = useQuery<AssistantIdentity, ApiError>({
    queryKey: ["assistant-identity"],
    queryFn: () => apiFetch<AssistantIdentity>("/assistant/identity"),
    enabled: isTenantAdmin && assistantEnabled,
    refetchOnWindowFocus: false,
    retry: false,
  });

  useEffect(() => {
    if (!seeded && identityQuery.data) {
      setValues(identityToFormValues(identityQuery.data));
      setSeeded(true);
    }
  }, [seeded, identityQuery.data]);

  const mutation = useMutation<AssistantIdentity, ApiError>({
    mutationFn: () =>
      apiFetch<AssistantIdentity>("/assistant/identity", {
        method: "PUT",
        body: toIdentityUpdate(values),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant-identity"], data);
      setValues(identityToFormValues(data));
    },
  });

  // Member / not-an-admin -> no access at all. The toggle endpoint is
  // tenant_admin-only, so a 403 THERE also means "not an admin". The
  // identity query's 403 is the toggle being OFF — that is NOT a no-access
  // case: an admin must still see the toggle to turn it on.
  const forbidden =
    !userLoading && (!isTenantAdmin || (toggleQuery.isError && toggleQuery.error?.status === 403));

  // --- No-access state (member). No identity form -> no assistant-name in the
  // DOM, matching the member case the e2e relies on. ---
  if (forbidden) {
    return <AssistantNoAccess />;
  }

  const errors: AssistantIdentityFormErrors = validateAssistantIdentity(values);
  const hasErrors = Object.keys(errors).length > 0;

  const update = <K extends keyof AssistantIdentityFormValues>(
    key: K,
    value: AssistantIdentityFormValues[K],
  ) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    mutation.reset();
  };

  const toggleTool = (name: string, checked: boolean) => {
    update(
      "enabledTools",
      checked ? [...values.enabledTools, name] : values.enabledTools.filter((t) => t !== name),
    );
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Identidad del asistente"
        description="Personaliza el nombre, el tono, el idioma y las herramientas de tu asistente personal."
        actions={
          <Button variant="outline" asChild>
            <Link href="/admin/assistant">
              <MessageSquare className="mr-2 h-4 w-4" />
              Ir al chat
            </Link>
          </Button>
        }
        data-testid="assistant-settings-header"
      />

      {/* On/off toggle — el control que enciende el asistente del tenant. */}
      <Card className="mt-6">
        <CardContent className="pt-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 space-y-1">
              <Label htmlFor="assistant-enabled-toggle" className="text-base">
                Asistente habilitado
              </Label>
              <p className="text-muted-foreground text-sm">
                Activa el asistente personal para tu organización. Mientras esté desactivado, nadie
                de tu tenant podrá usarlo y esta configuración permanece bloqueada.
              </p>
            </div>
            <label
              htmlFor="assistant-enabled-toggle"
              className="flex shrink-0 cursor-pointer items-center gap-2"
            >
              <span className="mt-0.5">
                <Checkbox
                  id="assistant-enabled-toggle"
                  data-testid="assistant-enabled-toggle"
                  checked={assistantEnabled}
                  disabled={toggleQuery.isLoading || toggleMutation.isPending}
                  onChange={(e) => toggleMutation.mutate(e.target.checked)}
                />
              </span>
              <span className="text-sm font-medium">
                {assistantEnabled ? "Activado" : "Desactivado"}
              </span>
            </label>
          </div>
          {toggleMutation.isError ? (
            <p className="text-destructive mt-3 text-sm" data-testid="assistant-enabled-error">
              {toggleMutation.error instanceof ApiError
                ? toggleMutation.error.body
                : String(toggleMutation.error)}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Configuración</CardTitle>
        </CardHeader>
        <CardContent>
          {userLoading || toggleQuery.isLoading || identityQuery.isLoading ? (
            <p className="text-muted-foreground flex items-center gap-2 text-sm">
              <Spinner />
              Cargando identidad…
            </p>
          ) : !assistantEnabled ? (
            <p className="text-muted-foreground text-sm" data-testid="assistant-identity-locked">
              Habilita el asistente para configurarlo.
            </p>
          ) : (
            <form
              className="space-y-6"
              data-testid="assistant-identity-form"
              onSubmit={(e) => {
                e.preventDefault();
                setTouched(true);
                if (hasErrors || mutation.isPending) return;
                mutation.mutate();
              }}
            >
              {/* Nombre — el objetivo principal del operador */}
              <div className="space-y-1.5">
                <Label htmlFor="assistant-name">Nombre</Label>
                <Input
                  id="assistant-name"
                  data-testid="assistant-name"
                  value={values.name}
                  maxLength={ASSISTANT_LIMITS.name.max}
                  onChange={(e) => update("name", e.target.value)}
                  placeholder="Asistente"
                  aria-invalid={touched && Boolean(errors.name)}
                />
                {touched && errors.name ? (
                  <p className="text-destructive text-xs" data-testid="assistant-name-error">
                    {errors.name}
                  </p>
                ) : null}
              </div>

              {/* Avatar */}
              <div className="space-y-1.5">
                <Label htmlFor="assistant-avatar">URL del avatar (opcional)</Label>
                <Input
                  id="assistant-avatar"
                  data-testid="assistant-avatar"
                  type="url"
                  value={values.avatarUrl}
                  maxLength={ASSISTANT_LIMITS.avatarUrl.max}
                  onChange={(e) => update("avatarUrl", e.target.value)}
                  placeholder="https://…"
                  aria-invalid={touched && Boolean(errors.avatarUrl)}
                />
                {touched && errors.avatarUrl ? (
                  <p className="text-destructive text-xs" data-testid="assistant-avatar-error">
                    {errors.avatarUrl}
                  </p>
                ) : null}
              </div>

              {/* Tono */}
              <div className="space-y-1.5">
                <Label htmlFor="assistant-tone">Tono</Label>
                <Input
                  id="assistant-tone"
                  data-testid="assistant-tone"
                  value={values.tone}
                  maxLength={ASSISTANT_LIMITS.tone.max}
                  onChange={(e) => update("tone", e.target.value)}
                  placeholder="profesional y conciso"
                  aria-invalid={touched && Boolean(errors.tone)}
                />
                {touched && errors.tone ? (
                  <p className="text-destructive text-xs" data-testid="assistant-tone-error">
                    {errors.tone}
                  </p>
                ) : null}
              </div>

              {/* Idioma — <select> con opciones es/en */}
              <div className="space-y-1.5">
                <Label htmlFor="assistant-language">Idioma</Label>
                <Select
                  id="assistant-language"
                  data-testid="assistant-language"
                  value={values.language}
                  onChange={(e) => update("language", e.target.value)}
                >
                  <option value="es">Español</option>
                  <option value="en">English</option>
                </Select>
              </div>

              {/* System prompt override */}
              <div className="space-y-1.5">
                <Label htmlFor="assistant-system-prompt">
                  Instrucciones adicionales (opcional)
                </Label>
                <textarea
                  id="assistant-system-prompt"
                  data-testid="assistant-system-prompt"
                  value={values.systemPrompt}
                  maxLength={ASSISTANT_LIMITS.systemPrompt.max}
                  onChange={(e) => update("systemPrompt", e.target.value)}
                  rows={5}
                  placeholder="Sustituye el cuerpo del prompt por defecto. La identidad (nombre, tono, idioma) se conserva."
                  className="border-input bg-background placeholder:text-muted-foreground focus-visible:ring-ring focus-visible:ring-offset-background flex w-full resize-y rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                />
                <p className="text-muted-foreground text-xs">
                  {values.systemPrompt.trim().length}/{ASSISTANT_LIMITS.systemPrompt.max}
                </p>
                {touched && errors.systemPrompt ? (
                  <p
                    className="text-destructive text-xs"
                    data-testid="assistant-system-prompt-error"
                  >
                    {errors.systemPrompt}
                  </p>
                ) : null}
              </div>

              {/* Herramientas habilitadas — catálogo amigable */}
              <fieldset className="space-y-3" data-testid="assistant-tools">
                <legend className="text-sm font-medium">Herramientas disponibles</legend>
                <p className="text-muted-foreground text-xs">
                  Datos de solo lectura que el asistente puede consultar para responderte.
                </p>
                <div className="space-y-2">
                  {ASSISTANT_TOOL_CATALOGUE.map((tool) => {
                    const checked = values.enabledTools.includes(tool.name);
                    const inputId = `assistant-tool-${tool.name}`;
                    return (
                      <label
                        key={tool.name}
                        htmlFor={inputId}
                        className="hover:bg-muted/50 flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors"
                      >
                        <span className="mt-0.5">
                          <Checkbox
                            id={inputId}
                            data-testid={`assistant-tool-${tool.name}`}
                            checked={checked}
                            onChange={(e) => toggleTool(tool.name, e.target.checked)}
                          />
                        </span>
                        <span className="min-w-0">
                          <span className="text-sm font-medium">{tool.label}</span>
                          <span className="text-muted-foreground block text-xs">
                            {tool.description}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              {/* Feedback */}
              {mutation.isError ? (
                <p className="text-destructive text-sm" data-testid="assistant-identity-error">
                  {mutation.error instanceof ApiError
                    ? mutation.error.body
                    : String(mutation.error)}
                </p>
              ) : mutation.isSuccess ? (
                <p
                  className="text-sm text-emerald-600"
                  data-testid="assistant-identity-saved"
                  role="status"
                >
                  Identidad guardada.
                </p>
              ) : null}

              <div className="flex justify-end">
                <Button
                  type="submit"
                  data-testid="assistant-identity-save"
                  disabled={mutation.isPending || (touched && hasErrors)}
                >
                  {mutation.isPending ? "Guardando…" : "Guardar"}
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AssistantNoAccess() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Asistente personal"
        data-testid="assistant-settings-header"
      />
      <EmptyState
        data-testid="assistant-no-access"
        icon={Bot}
        title="Asistente no disponible"
        description="El asistente personal es exclusivo para administradores del tenant y debe estar habilitado para tu organización."
      />
    </div>
  );
}
