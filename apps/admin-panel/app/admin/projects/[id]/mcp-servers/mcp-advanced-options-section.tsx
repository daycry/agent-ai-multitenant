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
          Opciones avanzadas
        </span>
        <span className="text-xs opacity-60">
          {state.auth_ref ? "credencial • " : ""}timeout {state.timeout_s}s
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-4">
          {appliedTemplate?.requires_auth && !showRawAuth ? (
            <div
              className="bg-success-soft text-success-soft-foreground rounded-md border border-success/30 p-3"
              data-testid="mcp-form-auth-managed"
            >
              <p className="text-sm font-medium">🔒 Esta integración requiere credencial</p>
              <p className="mt-1 text-xs">
                El sistema ya sabe dónde guardar el secreto. Pide al{" "}
                <strong>administrador del tenant</strong> que añada{" "}
                <code>{appliedTemplate.secret_keys.join(", ") || "la credencial"}</code> en Vault
                antes del primer uso. Mientras no esté, las llamadas a este MCP devolverán un error
                de autenticación tipado (no se cae el sistema).
              </p>
              <p className="mt-2 text-xs">
                <a
                  href="https://github.com/daycry/agent-ai-multitenant/blob/master/docs/03-guides/configurar-mcp-server.md"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  Ver guía de configuración →
                </a>
                {"  ·  "}
                <button
                  type="button"
                  onClick={() => onShowRawAuthChange(true)}
                  className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                  data-testid="mcp-form-show-raw-auth"
                >
                  Detalles técnicos
                </button>
              </p>
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="mcp-form-auth-ref">
                  {appliedTemplate?.requires_auth
                    ? "Ruta del secreto en Vault"
                    : "Credencial del servidor (opcional)"}
                </Label>
                {appliedTemplate?.requires_auth && showRawAuth && (
                  <button
                    type="button"
                    onClick={() => onShowRawAuthChange(false)}
                    className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                    data-testid="mcp-form-hide-raw-auth"
                  >
                    ← Ocultar detalles técnicos
                  </button>
                )}
              </div>
              <Input
                id="mcp-form-auth-ref"
                data-testid="mcp-form-auth-ref"
                value={state.auth_ref ?? ""}
                onChange={(e) => setAuthRefManual(e.target.value)}
                placeholder="vault:secret/data/mcp/<servicio>/<proyecto>"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                {appliedTemplate?.requires_auth
                  ? "El sistema rellena esta ruta automáticamente al aplicar una plantilla. Solo edítala si tu Vault tiene una convención distinta."
                  : "Solo para MCPs que necesitan API key / token. El admin del tenant guarda el secreto en Vault y aquí solo se referencia con la ruta vault:…"}
              </p>
            </div>
          )}

          <div>
            <Label htmlFor="mcp-form-timeout">Timeout (segundos)</Label>
            <Input
              id="mcp-form-timeout"
              data-testid="mcp-form-timeout"
              type="number"
              min={1}
              max={300}
              value={state.timeout_s}
              onChange={(e) => onChange({ ...state, timeout_s: Number(e.target.value) || 30 })}
            />
            <p className="text-muted-foreground mt-1 text-xs">
              Tiempo máximo por llamada. 30s va bien para la mayoría; sube a 120s para MCPs lentos
              como Docling o Puppeteer.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
