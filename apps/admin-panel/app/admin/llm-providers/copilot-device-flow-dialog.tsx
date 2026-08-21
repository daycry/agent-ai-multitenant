"use client";

/**
 * Device Flow de GitHub Copilot: start → mostrar `user_code` + enlace → poll en
 * el intervalo sugerido hasta que GitHub autorice.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid`, mismas transiciones de fase y mismo manejo del timer.
 *
 * El token acuñado NUNCA aparece en la UI: la respuesta del poll sólo trae un
 * `status` y un booleano `authorized`; el valor va directo a Vault.
 */

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { DeviceFlowPoll, DeviceFlowStart, LlmProvider } from "./llm-provider-types";

type DeviceFlowPhase = "idle" | "starting" | "polling" | "authorized" | "error";

interface CopilotDeviceFlowDialogProps {
  provider: LlmProvider;
  onClose: () => void;
  onAuthorized: () => void;
}

export function CopilotDeviceFlowDialog({
  provider,
  onClose,
  onAuthorized,
}: CopilotDeviceFlowDialogProps) {
  const t = useT("llmProviders");
  const errorText = useErrorText();
  const [phase, setPhase] = useState<DeviceFlowPhase>("idle");
  const [start, setStart] = useState<DeviceFlowStart | null>(null);
  const [pollStatus, setPollStatus] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string>("");
  // Hold the active poll timer so we can clear it on unmount / close.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  async function pollOnce(info: DeviceFlowStart, interval: number): Promise<void> {
    if (cancelledRef.current) return;
    try {
      const result = await apiFetch<DeviceFlowPoll>("/admin/llm/copilot/device-flow/poll", {
        method: "POST",
        body: {
          provider_id: provider.id,
          device_code: info.device_code,
          interval,
        },
      });
      if (cancelledRef.current) return;
      setPollStatus(result.status);
      if (result.authorized) {
        setPhase("authorized");
        return;
      }
      if (result.status === "expired" || result.status === "denied") {
        setErrorMessage(
          result.status === "expired" ? t("deviceFlowExpired") : t("deviceFlowDenied"),
        );
        setPhase("error");
        return;
      }
      // pending / slow_down → keep polling on the (possibly backed-off) interval.
      const next = result.interval ?? interval;
      timerRef.current = setTimeout(() => void pollOnce(info, next), next * 1000);
    } catch (err) {
      if (cancelledRef.current) return;
      setErrorMessage(errorText(err));
      setPhase("error");
    }
  }

  async function startFlow(): Promise<void> {
    setPhase("starting");
    setErrorMessage("");
    setPollStatus("");
    try {
      const info = await apiFetch<DeviceFlowStart>("/admin/llm/copilot/device-flow/start", {
        method: "POST",
        body: { provider_id: provider.id },
      });
      if (cancelledRef.current) return;
      setStart(info);
      setPhase("polling");
      timerRef.current = setTimeout(() => void pollOnce(info, info.interval), info.interval * 1000);
    } catch (err) {
      if (cancelledRef.current) return;
      setErrorMessage(errorText(err));
      setPhase("error");
    }
  }

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="md">
      <DialogContent data-testid="device-flow-dialog">
        <DialogHeader>
          <DialogTitle>{t("deviceFlowTitle", { name: provider.display_name })}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs">{t("deviceFlowIntro")}</p>

          {phase === "idle" ? (
            <Button onClick={() => void startFlow()} data-testid="device-flow-start">
              {t("deviceFlowStart")}
            </Button>
          ) : null}

          {phase === "starting" ? (
            <p className="text-muted-foreground text-sm" data-testid="device-flow-starting">
              {t("deviceFlowStarting")}
            </p>
          ) : null}

          {start && (phase === "polling" || phase === "authorized") ? (
            <div className="space-y-3" data-testid="device-flow-codes">
              <div className="space-y-1">
                <Label>{t("deviceFlowUserCode")}</Label>
                <p
                  className="bg-muted rounded-md px-3 py-2 text-center font-mono text-lg tracking-widest"
                  data-testid="device-flow-user-code"
                >
                  {start.user_code}
                </p>
              </div>
              <a
                href={start.verification_uri}
                target="_blank"
                rel="noreferrer noopener"
                className="text-primary inline-flex items-center gap-1 text-sm underline"
                data-testid="device-flow-verification-link"
              >
                {t("deviceFlowOpen", { uri: start.verification_uri })}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
              {phase === "polling" ? (
                <p
                  className="text-muted-foreground flex items-center gap-2 text-sm"
                  data-testid="device-flow-polling"
                >
                  {t("deviceFlowWaiting")}
                  {pollStatus === "slow_down" ? (
                    <span className="text-xs italic">{t("deviceFlowSlowDown")}</span>
                  ) : null}
                </p>
              ) : null}
            </div>
          ) : null}

          {phase === "authorized" ? (
            <div
              className="border-success/40 bg-success-soft flex items-center gap-2 rounded-lg border p-3"
              data-testid="device-flow-authorized"
            >
              <CheckCircle2 className="text-success h-4 w-4 shrink-0" />
              <p className="text-sm">{t("deviceFlowAuthorized")}</p>
            </div>
          ) : null}

          {phase === "error" ? (
            <p className="text-destructive text-sm" data-testid="device-flow-error">
              {errorMessage}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          {phase === "authorized" ? (
            <Button onClick={onAuthorized} data-testid="device-flow-done">
              {t("deviceFlowDone")}
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={onClose} data-testid="device-flow-cancel">
                {t("cancel")}
              </Button>
              {phase === "error" ? (
                <Button onClick={() => void startFlow()} data-testid="device-flow-retry">
                  {t("deviceFlowRetry")}
                </Button>
              ) : null}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
