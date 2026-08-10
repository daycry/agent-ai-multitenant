"use client";

/**
 * URL base pública + prefijo de API + la callback derivada que el operador
 * registra en el IdP (ADR 0047 y ADR 0069).
 *
 * Sale de `page.tsx` en `task_prod16_08` sin tocar comportamiento: es la mitad
 * de la pantalla y no comparte estado con la ficha ni con el diálogo.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

import type { ApiPathPrefix, PublicBaseUrl } from "./sso-types";

export function CallbackUrlCard({ url, loading }: { url: string | null; loading: boolean }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // ADR 0069: el prefijo de API (p.ej. /api) bajo un reverse proxy single-origin.
  const [prefixDraft, setPrefixDraft] = useState<string | null>(null);
  const [prefixSaveError, setPrefixSaveError] = useState<string | null>(null);

  // Authoritative source: the effective public base URL + whether it is the
  // operator override or still the env bootstrap default (ADR 0047).
  const baseQuery = useQuery({
    queryKey: ["sso-public-base-url"],
    queryFn: () => apiFetch<PublicBaseUrl>("/auth/sso/public-base-url"),
    refetchOnWindowFocus: false,
  });

  const saveMutation = useMutation({
    mutationFn: (base_url: string) =>
      apiFetch<PublicBaseUrl>("/auth/sso/public-base-url", {
        method: "PUT",
        body: { base_url },
      }),
    onSuccess: () => {
      setSaveError(null);
      setDraft(null);
      // Re-read the base + the derived callback URL (it depends on the base).
      void queryClient.invalidateQueries({ queryKey: ["sso-public-base-url"] });
      void queryClient.invalidateQueries({ queryKey: ["sso-callback-url"] });
    },
    onError: (err) => setSaveError(err instanceof ApiError ? err.body : String(err)),
  });

  // ADR 0069: prefijo de API (origen + prefijo + path SSO), editable por separado.
  const prefixQuery = useQuery({
    queryKey: ["sso-api-path-prefix"],
    queryFn: () => apiFetch<ApiPathPrefix>("/auth/sso/api-path-prefix"),
    refetchOnWindowFocus: false,
  });
  const prefixSaveMutation = useMutation({
    mutationFn: (prefix: string) =>
      apiFetch<ApiPathPrefix>("/auth/sso/api-path-prefix", {
        method: "PUT",
        body: { prefix },
      }),
    onSuccess: () => {
      setPrefixSaveError(null);
      setPrefixDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["sso-api-path-prefix"] });
      void queryClient.invalidateQueries({ queryKey: ["sso-callback-url"] });
    },
    onError: (err) => setPrefixSaveError(err instanceof ApiError ? err.body : String(err)),
  });

  const baseData = baseQuery.data;
  const fieldValue = draft ?? baseData?.base_url ?? "";
  const dirty =
    draft !== null && draft.trim() !== "" && draft.trim() !== (baseData?.base_url ?? "");
  const stillDefault = baseData ? !baseData.is_override : false;

  const prefixData = prefixQuery.data;
  // El prefijo "" es válido (sin prefijo), así que el dirty compara contra el valor actual.
  const prefixValue = prefixDraft ?? prefixData?.prefix ?? "";
  const prefixDirty = prefixDraft !== null && prefixDraft.trim() !== (prefixData?.prefix ?? "");

  async function copy() {
    if (url === null) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore silently.
    }
  }

  return (
    <Card className="mt-6" data-testid="sso-callback-card">
      <CardHeader>
        <CardTitle className="text-base">URL base pública de la aplicación</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          La URL pública única de la plataforma (p. ej.{" "}
          <span className="font-mono">https://agentic-orchestrator.com</span>). De ella se derivan
          la <strong>callback de SSO</strong> y el <strong>ACS de SAML</strong> como rutas — el
          puerto queda detrás de tu gateway en producción. Es global (una para toda la plataforma).
        </p>

        {/* Editable base URL — System Admin only (the backend gates the PUT). */}
        <RoleGuard
          min="system_admin"
          fallback={
            <p className="text-muted-foreground text-xs" data-testid="sso-redirect-base">
              Base pública actual:{" "}
              <span className="font-mono">
                {baseQuery.isLoading ? "…" : (baseData?.base_url ?? "—")}
              </span>
            </p>
          }
        >
          <div className="space-y-1.5">
            <Label htmlFor="public-base-url">URL base pública</Label>
            <div className="flex items-center gap-2">
              <Input
                id="public-base-url"
                value={fieldValue}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="https://tu-dominio.com"
                data-testid="sso-public-base-url-input"
                disabled={baseQuery.isLoading || saveMutation.isPending}
              />
              <Button
                type="button"
                size="sm"
                onClick={() => saveMutation.mutate(fieldValue.trim())}
                disabled={!dirty || saveMutation.isPending}
                data-testid="sso-public-base-url-save"
              >
                {saveMutation.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
            {saveError && (
              <p
                className="text-danger-soft-foreground text-xs"
                data-testid="sso-public-base-url-error"
              >
                {saveError}
              </p>
            )}
          </div>

          {/* ADR 0069: prefijo de API bajo el reverse proxy single-origin. */}
          <div className="space-y-1.5">
            <Label htmlFor="api-path-prefix">Prefijo de API (reverse proxy)</Label>
            <div className="flex items-center gap-2">
              <Input
                id="api-path-prefix"
                value={prefixValue}
                onChange={(e) => setPrefixDraft(e.target.value)}
                placeholder="/api  (vacío si el API cuelga de la raíz)"
                data-testid="sso-api-path-prefix-input"
                disabled={prefixQuery.isLoading || prefixSaveMutation.isPending}
              />
              <Button
                type="button"
                size="sm"
                onClick={() => prefixSaveMutation.mutate(prefixValue.trim())}
                disabled={!prefixDirty || prefixSaveMutation.isPending}
                data-testid="sso-api-path-prefix-save"
              >
                {prefixSaveMutation.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              Si publicas single-origin (SPA en <span className="font-mono">/</span> y API bajo{" "}
              <span className="font-mono">/api</span> tras Caddy/nginx), pon{" "}
              <span className="font-mono">/api</span>. Vacío si el api-server cuelga de la raíz del
              dominio. Se inserta entre el origen y la ruta del callback.
            </p>
            {prefixSaveError && (
              <p
                className="text-danger-soft-foreground text-xs"
                data-testid="sso-api-path-prefix-error"
              >
                {prefixSaveError}
              </p>
            )}
          </div>
        </RoleGuard>

        {/* The derived callback URL the operator registers at the IdP. */}
        <div>
          <Label className="text-xs">URL de callback / redirect (a registrar en el IdP)</Label>
          <div className="mt-1 flex items-center gap-2">
            <code
              className="bg-muted/40 flex-1 break-all rounded-md border px-3 py-2 font-mono text-xs"
              data-testid="sso-callback-url"
            >
              {loading ? "Cargando…" : (url ?? "—")}
            </code>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={copy}
              disabled={url === null}
              data-testid="sso-callback-copy"
              aria-label="Copiar URL de callback"
            >
              <Copy className="h-3.5 w-3.5" />
              {copied ? "Copiado" : "Copiar"}
            </Button>
          </div>
        </div>

        {stillDefault && (
          <p
            className="bg-warning-soft text-warning-soft-foreground border-warning/30 rounded-md border px-3 py-2 text-xs"
            data-testid="sso-redirect-base-warning"
            role="alert"
          >
            Sigue usando el valor de arranque{" "}
            <span className="font-mono">{baseData?.env_default}</span> (apunta al api-server local,
            no a tu dominio público). Pon arriba tu URL pública real antes de registrar la callback
            en el IdP.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
