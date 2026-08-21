"use client";

// ADR 0130: botón de app-preview ON-DEMAND. Levanta la app del proyecto (rama
// por defecto) o de un plan (su rama) durante 24h reutilizando la maquinaria de
// review-runtime — sin veredicto, solo la app en vivo. POST /{scope}/{id}/preview
// encola el spawn; el componente hace polling de GET /{scope}/{id}/preview-session
// hasta que la URL firmada de la app está lista, y la abre en una pestaña nueva.
//
// i18n (prod-16 `task_prod16_03`): el título y la descripción salen del
// diccionario ELEGIDOS POR `scope`, no de un prop. Antes el título era
// `title?: string` y las dos pantallas que montan el componente le pasaban su
// literal castellano, así que traducir el componente no habría traducido
// ninguna de las dos: la deuda vivía en el llamante.

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, MonitorPlay } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface PreviewSession {
  session_id: string;
  status: string;
  app_url: string;
  expires_at: string | null;
  app_configured: boolean;
}

interface PreviewLauncherProps {
  scope: "projects" | "plans";
  id: string;
}

const _POLL_MS = 3000;
const _MAX_POLLS = 40; // ~2 min

export function PreviewLauncher({ scope, id }: PreviewLauncherProps) {
  const queryClient = useQueryClient();
  const t = useT("previewLauncher");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const [polling, setPolling] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const pollsRef = useRef(0);

  const base = `/${scope}/${id}`;
  const queryKey = ["preview-session", scope, id];

  const sessionQuery = useQuery<PreviewSession | null, ApiError>({
    queryKey,
    queryFn: async () => {
      try {
        return await apiFetch<PreviewSession>(`${base}/preview-session`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null; // no live preview yet
        throw e;
      }
    },
    refetchOnWindowFocus: false,
    refetchInterval: () => (polling ? _POLL_MS : false),
  });

  const live = sessionQuery.data ?? null;

  // Stop polling once the app URL is ready (or we've waited long enough).
  useEffect(() => {
    if (!polling) return;
    if (live?.app_url) {
      setPolling(false);
      return;
    }
    pollsRef.current += 1;
    if (pollsRef.current >= _MAX_POLLS) {
      setPolling(false);
      setErrorMsg(t("slow"));
    }
  }, [polling, live, sessionQuery.dataUpdatedAt, t]);

  const launch = useMutation<{ status: string; app_url?: string }, ApiError, void>({
    mutationFn: () => apiFetch(`${base}/preview`, { method: "POST" }),
    onSuccess: (res) => {
      setErrorMsg(null);
      if (res.status === "running" && res.app_url) {
        void queryClient.invalidateQueries({ queryKey });
        return;
      }
      pollsRef.current = 0;
      setPolling(true);
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (e) => setErrorMsg(errorText(e)),
  });

  const openApp = useCallback(() => {
    if (live?.app_url) window.open(live.app_url, "_blank", "noopener,noreferrer");
  }, [live]);

  const expires =
    live?.expires_at != null
      ? new Date(live.expires_at).toLocaleString(tCommon("dateLocale"))
      : null;
  const busy = launch.isPending || polling;

  return (
    <Card data-testid="preview-launcher-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <MonitorPlay className="text-muted-foreground h-5 w-5" />
        <CardTitle>{scope === "plans" ? t("titlePlan") : t("titleProject")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          {scope === "plans" ? t("descriptionPlan") : t("descriptionProject")}
        </p>

        <div className="flex flex-wrap items-center gap-3">
          {live?.app_url ? (
            <Button onClick={openApp} data-testid="preview-open-app">
              <ExternalLink className="mr-1 h-4 w-4" />
              {t("openApp")}
            </Button>
          ) : null}
          <Button
            variant={live?.app_url ? "outline" : "default"}
            onClick={() => launch.mutate()}
            disabled={busy}
            data-testid="preview-launch"
          >
            {busy ? t("launching") : live?.app_url ? t("relaunch") : t("launch")}
          </Button>
          {polling ? (
            <span className="text-muted-foreground text-xs" data-testid="preview-provisioning">
              {t("provisioning")}
            </span>
          ) : null}
          {expires ? (
            <span className="text-muted-foreground text-xs">{t("expires", { at: expires })}</span>
          ) : null}
        </div>

        {errorMsg ? (
          <p className="text-destructive text-xs" data-testid="preview-error">
            {errorMsg}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
