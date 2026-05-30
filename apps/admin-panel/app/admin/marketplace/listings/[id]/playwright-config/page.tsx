"use client";

/**
 * task_09_13 — Configuración guiada de la tool destacada Playwright.
 *
 * Playwright es la tool destacada del marketplace: un listing GLOBAL
 * verificado (tenant_id NULL) cuyo manifest incluye un `config_schema`
 * con la configuración guiada. Esta pantalla lee ese esquema del listing y
 * renderiza un formulario guiado (no YAML libre) que captura:
 *
 *   - browsers (chromium / firefox / webkit, multi-selección),
 *   - headless (toggle),
 *   - screenshots (off / on / only-on-failure),
 *   - traces (off / on / retain-on-failure),
 *   - base_url (texto),
 *   - timeout_ms (número, entero positivo).
 *
 * Valida la selección en cliente (mismas reglas que
 * marketplace/playwright.py::PlaywrightToolConfig) y muestra el objeto de
 * configuración resultante que se persistirá en la instalación del tenant.
 *
 * Endpoint backend (routers/marketplace.py, RLS + RBAC):
 *   GET /marketplace/listings/{id}  — el listing con su manifest.config_schema
 *
 * El listing es de solo lectura (catálogo global); la configuración elegida
 * vive en la instalación tenant-scoped, nunca en el listing compartido.
 */

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types — mirror api_server.marketplace.playwright
// --------------------------------------------------------------------------
const BROWSERS = ["chromium", "firefox", "webkit"] as const;
type Browser = (typeof BROWSERS)[number];

const SCREENSHOT_MODES = ["off", "on", "only-on-failure"] as const;
type ScreenshotMode = (typeof SCREENSHOT_MODES)[number];

const TRACE_MODES = ["off", "on", "retain-on-failure"] as const;
type TraceMode = (typeof TRACE_MODES)[number];

interface MarketplaceListing {
  id: string;
  name: string;
  version: string;
  kind: string;
  trust_level: string;
  tenant_id: string | null;
  manifest: { config_schema?: Record<string, unknown> } & Record<string, unknown>;
}

interface PlaywrightConfig {
  browsers: Browser[];
  headless: boolean;
  screenshots: ScreenshotMode;
  traces: TraceMode;
  base_url: string | null;
  timeout_ms: number;
}

const DEFAULT_CONFIG: PlaywrightConfig = {
  browsers: ["chromium"],
  headless: true,
  screenshots: "only-on-failure",
  traces: "retain-on-failure",
  base_url: null,
  timeout_ms: 30000,
};

const SCREENSHOT_LABEL: Record<ScreenshotMode, string> = {
  off: "Desactivado",
  on: "Siempre",
  "only-on-failure": "Solo en fallo",
};

const TRACE_LABEL: Record<TraceMode, string> = {
  off: "Desactivado",
  on: "Siempre",
  "retain-on-failure": "Retener en fallo",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function PlaywrightConfigPage() {
  const params = useParams<{ id: string }>();
  const listingId = params.id;

  const listingQuery = useQuery({
    queryKey: ["marketplace-listing", listingId],
    queryFn: () => apiFetch<MarketplaceListing>(`/marketplace/listings/${listingId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(listingId),
  });

  const [config, setConfig] = useState<PlaywrightConfig>(DEFAULT_CONFIG);

  // Client-side validation mirrors PlaywrightToolConfig.from_dict.
  const error = useMemo<string | null>(() => {
    if (config.browsers.length === 0) return "Selecciona al menos un navegador.";
    if (!Number.isInteger(config.timeout_ms) || config.timeout_ms <= 0)
      return "El timeout debe ser un entero positivo (ms).";
    return null;
  }, [config]);

  function toggleBrowser(browser: Browser) {
    setConfig((prev) => {
      const has = prev.browsers.includes(browser);
      const browsers = has
        ? prev.browsers.filter((b) => b !== browser)
        : [...prev.browsers, browser];
      return { ...prev, browsers };
    });
  }

  const data = listingQuery.data;
  const hasGuidedSchema = Boolean(data?.manifest?.config_schema);

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="playwright-config-page"
    >
      <PageHeader
        icon={<Clapperboard className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Configuración de Playwright"
        description="Configura cómo se ejecuta la tool Playwright (navegadores, headless, screenshots, traces). La configuración se guarda en la instalación de tu tenant."
        data-testid="playwright-config-header"
        actions={
          data ? (
            <Badge variant="info" data-testid="playwright-listing-version">
              {data.name} {data.version}
            </Badge>
          ) : null
        }
      />

      {listingQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="playwright-loading">
          Cargando…
        </p>
      ) : listingQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="playwright-error">
          {listingQuery.error instanceof ApiError
            ? listingQuery.error.body
            : String(listingQuery.error)}
        </p>
      ) : data && !hasGuidedSchema ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="playwright-no-schema">
          Este listing no define una configuración guiada.
        </p>
      ) : data ? (
        <div className="mt-6 space-y-4">
          {/* Browsers (multi-select) */}
          <Card data-testid="playwright-field-browsers">
            <CardHeader>
              <CardTitle className="text-base">Navegadores</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {BROWSERS.map((browser) => {
                const selected = config.browsers.includes(browser);
                return (
                  <Button
                    key={browser}
                    variant={selected ? "default" : "outline"}
                    size="sm"
                    onClick={() => toggleBrowser(browser)}
                    data-testid={`playwright-browser-${browser}`}
                    aria-pressed={selected}
                  >
                    {browser}
                  </Button>
                );
              })}
            </CardContent>
          </Card>

          {/* Headless toggle */}
          <Card data-testid="playwright-field-headless">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">Headless</CardTitle>
              <Button
                variant={config.headless ? "default" : "outline"}
                size="sm"
                onClick={() => setConfig((prev) => ({ ...prev, headless: !prev.headless }))}
                data-testid="playwright-headless-toggle"
                aria-pressed={config.headless}
              >
                {config.headless ? "Sí" : "No"}
              </Button>
            </CardHeader>
          </Card>

          {/* Screenshots */}
          <Card data-testid="playwright-field-screenshots">
            <CardHeader>
              <CardTitle className="text-base">Screenshots</CardTitle>
            </CardHeader>
            <CardContent>
              <select
                className="border-input bg-background flex h-10 w-full rounded-md border px-3 py-2 text-sm"
                value={config.screenshots}
                onChange={(e) =>
                  setConfig((prev) => ({
                    ...prev,
                    screenshots: e.target.value as ScreenshotMode,
                  }))
                }
                data-testid="playwright-screenshots-select"
              >
                {SCREENSHOT_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {SCREENSHOT_LABEL[mode]}
                  </option>
                ))}
              </select>
            </CardContent>
          </Card>

          {/* Traces */}
          <Card data-testid="playwright-field-traces">
            <CardHeader>
              <CardTitle className="text-base">Traces</CardTitle>
            </CardHeader>
            <CardContent>
              <select
                className="border-input bg-background flex h-10 w-full rounded-md border px-3 py-2 text-sm"
                value={config.traces}
                onChange={(e) =>
                  setConfig((prev) => ({ ...prev, traces: e.target.value as TraceMode }))
                }
                data-testid="playwright-traces-select"
              >
                {TRACE_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {TRACE_LABEL[mode]}
                  </option>
                ))}
              </select>
            </CardContent>
          </Card>

          {/* base_url + timeout_ms */}
          <Card data-testid="playwright-field-advanced">
            <CardHeader>
              <CardTitle className="text-base">Avanzado</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1">
                <Label htmlFor="playwright-base-url">Base URL</Label>
                <Input
                  id="playwright-base-url"
                  placeholder="https://staging.example.test"
                  value={config.base_url ?? ""}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      base_url: e.target.value.trim() === "" ? null : e.target.value,
                    }))
                  }
                  data-testid="playwright-base-url"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="playwright-timeout">Timeout (ms)</Label>
                <Input
                  id="playwright-timeout"
                  type="number"
                  min={1}
                  value={String(config.timeout_ms)}
                  onChange={(e) =>
                    setConfig((prev) => ({
                      ...prev,
                      timeout_ms: Number.parseInt(e.target.value, 10),
                    }))
                  }
                  data-testid="playwright-timeout"
                />
              </div>
            </CardContent>
          </Card>

          {error ? (
            <p className="text-destructive text-sm" data-testid="playwright-config-validation">
              {error}
            </p>
          ) : null}

          {/* The resulting config (persisted on the tenant install). */}
          <Card data-testid="playwright-config-preview-card">
            <CardHeader>
              <CardTitle className="text-base">Configuración resultante</CardTitle>
            </CardHeader>
            <CardContent>
              <pre
                className="bg-muted overflow-x-auto rounded p-3 text-xs"
                data-testid="playwright-config-preview"
              >
                {JSON.stringify(config, null, 2)}
              </pre>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
