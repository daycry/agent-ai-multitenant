"use client";

/**
 * Metadatos del SP (EntityID + ACS) que el operador registra en el IdP, con
 * copiado al portapapeles. Sale de `page.tsx` en `task_prod16_08`.
 */

import { useState } from "react";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  isDefaultRedirectBase,
  redirectBaseFromUrl,
  SSO_REDIRECT_BASE_DEFAULT,
} from "@/lib/sso-redirect-base";

import type { SpMetadata } from "./saml-types";

// --------------------------------------------------------------------------
// SP metadata card — EntityID + ACS URL to register at the IdP
// --------------------------------------------------------------------------
export function SpMetadataCard({
  metadata,
  loading,
}: {
  metadata: SpMetadata | null;
  loading: boolean;
}) {
  // The SP EntityID + ACS URL are both built from `sso_redirect_base_url`;
  // if the ACS still carries the backend default placeholder, warn the
  // operator to set the real public base before wiring up the IdP
  // (ADR 0047 §6). The SP identity (entityID + ACS) is platform-global.
  const acsUrl = loading ? null : (metadata?.acs_url ?? null);
  const isPlaceholder = !loading && isDefaultRedirectBase(acsUrl);
  const base = redirectBaseFromUrl(acsUrl);

  return (
    <Card className="mt-6" data-testid="saml-sp-metadata-card">
      <CardHeader>
        <CardTitle className="text-base">Metadatos del SP (este sistema)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          Estos valores son <strong>globales</strong> (una sola identidad de SP para toda la
          plataforma). Regístralos en tu proveedor de identidad SAML: la <strong>Entity ID</strong>{" "}
          del SP y la <strong>URL de ACS</strong> (Assertion Consumer Service) a la que el IdP
          enviará la respuesta.
        </p>
        <CopyRow
          label="SP Entity ID"
          value={loading ? null : (metadata?.sp_entity_id ?? null)}
          testid="saml-sp-entity-id"
        />
        <CopyRow label="ACS URL" value={acsUrl} testid="saml-acs-url" />
        {base !== null && !loading ? (
          <p className="text-muted-foreground text-xs" data-testid="saml-redirect-base">
            Base pública configurada: <span className="font-mono">{base}</span>
          </p>
        ) : null}
        {isPlaceholder ? (
          <p
            className="border-warning/40 bg-warning/10 text-warning-foreground rounded-md border px-3 py-2 text-xs"
            data-testid="saml-redirect-base-warning"
            role="alert"
          >
            Sigue usando la base por defecto{" "}
            <span className="font-mono">{SSO_REDIRECT_BASE_DEFAULT}</span> (un marcador de posición,
            ni siquiera coincide con el api-server de desarrollo). Configura{" "}
            <span className="font-mono">SSO_REDIRECT_BASE_URL</span> con tu URL pública antes de
            registrar el ACS en el IdP.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CopyRow({
  label,
  value,
  testid,
}: {
  label: string;
  value: string | null;
  testid: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (value === null) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); ignore silently.
    }
  }

  return (
    <div>
      <Label className="text-muted-foreground text-xs">{label}</Label>
      <div className="mt-1 flex items-center gap-2">
        <code
          className="bg-muted/40 flex-1 break-all rounded-md border px-3 py-2 font-mono text-xs"
          data-testid={testid}
        >
          {value ?? "Cargando…"}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={copy}
          disabled={value === null}
          data-testid={`${testid}-copy`}
          aria-label={`Copiar ${label}`}
        >
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Copiado" : "Copiar"}
        </Button>
      </div>
    </div>
  );
}
