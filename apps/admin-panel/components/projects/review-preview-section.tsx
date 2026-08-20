"use client";

// hallazgo #4 (QA 2026-07-07): no existía NINGUNA superficie para configurar la
// imagen del app-preview del review-runtime (repository_config.review_image) —
// el operador se topaba con un error críptico al abrir la app de una sesión de
// validación humana. Esta sección edita repository_config.review_image +
// review_port con ayuda inline (ADR 0063: la imagen la construye la CI del
// propio proyecto; la plataforma solo la referencia).

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MonitorPlay } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface ReviewPreviewSectionProps {
  projectId: string;
  /** repository_config actual del proyecto (null = nunca configurado). */
  value: Record<string, unknown> | null;
}

export function ReviewPreviewSection({ projectId, value }: ReviewPreviewSectionProps) {
  const queryClient = useQueryClient();
  const t = useT("projectReviewPreview");
  const errorText = useErrorText();
  const [image, setImage] = useState(
    typeof value?.["review_image"] === "string" ? (value["review_image"] as string) : "",
  );
  const [port, setPort] = useState(
    value?.["review_port"] != null ? String(value["review_port"]) : "",
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: async () => {
      // Merge sobre el blob existente: esta sección solo posee sus 2 claves.
      const next: Record<string, unknown> = { ...(value ?? {}) };
      const trimmed = image.trim();
      if (trimmed) {
        next["review_image"] = trimmed;
      } else {
        delete next["review_image"];
      }
      const portNum = Number.parseInt(port, 10);
      if (port.trim() && Number.isFinite(portNum) && portNum > 0 && portNum <= 65535) {
        next["review_port"] = portNum;
      } else {
        delete next["review_port"];
      }
      return apiFetch(`/projects/${projectId}`, {
        method: "PUT",
        body: { repository_config: next },
      });
    },
    onSuccess: () => {
      setErrorMsg(null);
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => {
      setSaved(false);
      setErrorMsg(errorText(e));
    },
  });

  const portInvalid =
    port.trim() !== "" &&
    (!Number.isFinite(Number.parseInt(port, 10)) ||
      Number.parseInt(port, 10) <= 0 ||
      Number.parseInt(port, 10) > 65535);

  return (
    <Card data-testid="review-preview-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <MonitorPlay className="text-muted-foreground h-5 w-5" />
        <CardTitle>{t("title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-muted-foreground text-sm">{t("description")}</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_10rem]">
          <div className="space-y-1.5">
            <Label htmlFor="review-image">{t("imageLabel")}</Label>
            <Input
              id="review-image"
              data-testid="review-image-input"
              placeholder="mi-app-preview:latest"
              value={image}
              onChange={(e) => {
                setSaved(false);
                setImage(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">{t("imageHint")}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="review-port">{t("portLabel")}</Label>
            <Input
              id="review-port"
              data-testid="review-port-input"
              placeholder="8080"
              inputMode="numeric"
              value={port}
              onChange={(e) => {
                setSaved(false);
                setPort(e.target.value);
              }}
            />
            <p className="text-muted-foreground text-xs">{t("portHint")}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || portInvalid}
            data-testid="review-preview-save"
          >
            {t("save")}
          </Button>
          {portInvalid ? <p className="text-destructive text-xs">{t("portInvalid")}</p> : null}
          {saved ? <p className="text-success text-xs">{t("saved")}</p> : null}
          {errorMsg ? (
            <p className="text-destructive text-xs" data-testid="review-preview-error">
              {errorMsg}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
