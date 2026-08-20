"use client";

/**
 * Opciones avanzadas del formulario de MCP server: la credencial y el timeout.
 *
 * Troceado de `mcp-server-dialog.tsx` en prod-16 `task_prod16_07`.
 *
 * **Por qué esta parte se puede separar.** El diálogo llevaba desde el
 * 2026-08-10 anotado en `SECTION_ALLOWLIST` con la razón escrita: era un
 * formulario con una decena de `useState` entrelazados y partirlo a lo bruto
 * salía prop-drilling. Este bloque es la excepción barata: 112 líneas de JSX que
 * sólo tocan dos campos del server (`auth_ref` y `timeout_s`) y se comunican con
 * el resto por un `onChange`. Un nivel de props, no cinco.
 *
 * `appliedTemplate` entra como dato y sale como aviso: editar la ruta de Vault a
 * mano rompe la invariante de «gestionado por plantilla», así que el bloque no
 * decide nada sobre la plantilla — llama a `onManualAuthEdit` y el diálogo, que
 * es quien la tiene, la limpia.
 *
 * **`showRawAuth` se queda en el diálogo, y eso es a propósito.** Mirándolo por
 * encima parece estado local de aquí —sólo se pinta aquí—, pero el diálogo lo
 * REINICIA en dos sitios: al aplicar una plantilla nueva y al reabrirse con otro
 * server. Bajarlo aquí obligaba a inventar un mecanismo para eso (un `key` que
 * remonta —y pierde el foco a media escritura—, o un `useEffect` que dispara
 * también cuando la plantilla pasa a null, que en el original no pasa). Dos
 * props es más barato que cualquiera de las dos, y no cambia el comportamiento
 * ni en un caso.
 */

import { ChevronDown, ChevronRight } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/lib/i18n";
import { type McpCatalogEntry, type McpServerConfig } from "./mcp-server-types";

export function McpAdvancedOptionsSection({
  state,
  onChange,
  appliedTemplate,
  onManualAuthEdit,
  open,
  onOpenChange,
  showRawAuth,
  onShowRawAuthChange,
}: {
  state: McpServerConfig;
  onChange: (next: McpServerConfig) => void;
  appliedTemplate: McpCatalogEntry | null;
  onManualAuthEdit: () => void;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  showRawAuth: boolean;
  onShowRawAuthChange: (next: boolean) => void;
}) {
  const t = useT("mcpServers");

  // Any manual edit of auth_ref breaks the "managed by template"
  // invariant — tell the dialog so it drops the appliedTemplate marker
  // and the raw input takes over again.
  function setAuthRefManual(value: string) {
    onChange({ ...state, auth_ref: value });
    if (appliedTemplate) onManualAuthEdit();
  }

  return (
    <div className="border-t pt-3">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        data-testid="mcp-form-advanced-toggle"
        aria-expanded={open}
        className="text-muted-foreground hover:text-foreground flex w-full items-center justify-between text-sm font-medium transition-colors"
      >
        <span className="flex items-center gap-1.5">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {t("advancedTitle")}
        </span>
        <span className="text-xs opacity-60">
          {state.auth_ref ? `${t("advancedHasCredential")} • ` : ""}
          {t("advancedTimeoutSummary", { seconds: state.timeout_s })}
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-4">
          {appliedTemplate?.requires_auth && !showRawAuth ? (
            <div
              className="bg-success-soft text-success-soft-foreground rounded-md border border-success/30 p-3"
              data-testid="mcp-form-auth-managed"
            >
              <p className="text-sm font-medium">{t("authManagedTitle")}</p>
              <p className="mt-1 text-xs">
                {t("authManagedIntro")} <strong>{t("authManagedRole")}</strong>{" "}
                {t("authManagedAdd")}{" "}
                <code>
                  {appliedTemplate.secret_keys.join(", ") || t("authManagedFallbackKeys")}
                </code>{" "}
                {t("authManagedTail")}
              </p>
              <p className="mt-2 text-xs">
                <a
                  href="https://github.com/daycry/agent-ai-multitenant/blob/master/docs/03-guides/configurar-mcp-server.md"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  {t("authGuideLink")}
                </a>
                {"  ·  "}
                <button
                  type="button"
                  onClick={() => onShowRawAuthChange(true)}
                  className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                  data-testid="mcp-form-show-raw-auth"
                >
                  {t("authShowDetails")}
                </button>
              </p>
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="mcp-form-auth-ref">
                  {appliedTemplate?.requires_auth ? t("authRefLabelTemplate") : t("authRefLabel")}
                </Label>
                {appliedTemplate?.requires_auth && showRawAuth && (
                  <button
                    type="button"
                    onClick={() => onShowRawAuthChange(false)}
                    className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                    data-testid="mcp-form-hide-raw-auth"
                  >
                    {t("authHideDetails")}
                  </button>
                )}
              </div>
              <Input
                id="mcp-form-auth-ref"
                data-testid="mcp-form-auth-ref"
                value={state.auth_ref ?? ""}
                onChange={(e) => setAuthRefManual(e.target.value)}
                placeholder={t("authRefPlaceholder")}
              />
              <p className="text-muted-foreground mt-1 text-xs">
                {appliedTemplate?.requires_auth ? t("authRefHelpTemplate") : t("authRefHelp")}
              </p>
            </div>
          )}

          <div>
            <Label htmlFor="mcp-form-timeout">{t("timeoutLabel")}</Label>
            <Input
              id="mcp-form-timeout"
              data-testid="mcp-form-timeout"
              type="number"
              min={1}
              max={300}
              value={state.timeout_s}
              onChange={(e) => onChange({ ...state, timeout_s: Number(e.target.value) || 30 })}
            />
            <p className="text-muted-foreground mt-1 text-xs">{t("timeoutHelp")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
