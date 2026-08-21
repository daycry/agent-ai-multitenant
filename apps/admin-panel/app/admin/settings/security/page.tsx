"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { KeyRound, ShieldCheck, ShieldOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface MfaStatus {
  enrolled: boolean;
  confirmed: boolean;
  recovery_codes_remaining: number;
}

interface MfaEnrollment {
  secret: string;
  provisioning_uri: string;
  recovery_codes: string[];
}

/**
 * Seguridad de la cuenta — verificación en dos pasos (TOTP).
 *
 * UI del MFA del Plan 08 (tanda 2026-07-19): el backend ya gateaba el login
 * con `mfa_required` para usuarios con factor confirmado; esta pantalla es
 * el camino para llegar a ese estado. El QR es el `otpauth://` URI del
 * enroll renderizado con qrcode.react; los recovery codes se muestran UNA
 * sola vez (el backend solo persiste sus hashes).
 */
export default function SecuritySettingsPage() {
  const t = useT("settingsSecurity");
  const queryClient = useQueryClient();
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [confirmCode, setConfirmCode] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const status = useQuery({
    queryKey: ["mfa-totp-status"],
    queryFn: () => apiFetch<MfaStatus>("/auth/mfa/totp"),
  });

  const enroll = useMutation({
    mutationFn: () => apiFetch<MfaEnrollment>("/auth/mfa/totp/enroll", { method: "POST" }),
    onSuccess: (data) => setEnrollment(data),
  });

  const confirm = useMutation({
    mutationFn: (code: string) =>
      apiFetch<MfaStatus>("/auth/mfa/totp/confirm", { method: "POST", body: { code } }),
    onSuccess: (data) => {
      setEnrollment(null);
      setConfirmError(null);
      queryClient.setQueryData(["mfa-totp-status"], data);
    },
    onError: () => setConfirmError(t("confirmError")),
  });

  const disable = useMutation({
    mutationFn: () => apiFetch<void>("/auth/mfa/totp", { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["mfa-totp-status"] }),
  });

  const active = status.data?.confirmed === true;

  return (
    <main className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-muted-foreground text-sm">{t("description")}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5" aria-hidden="true" />
            {t("cardTitle")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {status.isLoading && <Spinner className="h-5 w-5" />}

          {active && !enrollment && (
            <div className="space-y-4" data-testid="mfa-status-on">
              <p className="flex items-center gap-2 text-sm">
                <ShieldCheck className="h-5 w-5 text-emerald-600" aria-hidden="true" />
                {t("on")}
              </p>
              <p className="text-muted-foreground text-sm">
                {t("recoveryLeft", { n: status.data?.recovery_codes_remaining ?? 0 })}
              </p>
              <Button
                variant="destructive"
                data-testid="mfa-disable-button"
                disabled={disable.isPending}
                onClick={() => disable.mutate()}
              >
                {disable.isPending && <Spinner className="mr-2 h-4 w-4" />}
                {t("disable")}
              </Button>
            </div>
          )}

          {!active && !enrollment && !status.isLoading && (
            <div className="space-y-4" data-testid="mfa-status-off">
              <p className="flex items-center gap-2 text-sm">
                <ShieldOff className="text-muted-foreground h-5 w-5" aria-hidden="true" />
                {t("off")}
              </p>
              <Button
                data-testid="mfa-enroll-button"
                disabled={enroll.isPending}
                onClick={() => enroll.mutate()}
              >
                {enroll.isPending && <Spinner className="mr-2 h-4 w-4" />}
                {t("enroll")}
              </Button>
            </div>
          )}

          {enrollment && (
            <div className="space-y-5" data-testid="mfa-enrollment">
              <div className="space-y-2">
                <p className="text-sm font-medium">{t("step1")}</p>
                <div className="bg-white inline-block rounded-lg p-3" data-testid="mfa-qr">
                  <QRCodeSVG value={enrollment.provisioning_uri} size={168} />
                </div>
                <p className="text-muted-foreground text-xs">
                  {t("manualKey")} <code className="font-mono">{enrollment.secret}</code>
                </p>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">{t("step2")}</p>
                <ul className="grid grid-cols-2 gap-1 font-mono text-sm" data-testid="mfa-recovery">
                  {enrollment.recovery_codes.map((code) => (
                    <li key={code}>{code}</li>
                  ))}
                </ul>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">{t("step3")}</p>
                <div className="flex items-end gap-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="mfa-confirm">{t("codeLabel")}</Label>
                    <Input
                      id="mfa-confirm"
                      data-testid="mfa-confirm-input"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="123456"
                      value={confirmCode}
                      onChange={(e) => setConfirmCode(e.target.value)}
                    />
                  </div>
                  <Button
                    data-testid="mfa-confirm-button"
                    disabled={confirm.isPending || confirmCode.trim().length === 0}
                    onClick={() => confirm.mutate(confirmCode.trim())}
                  >
                    {confirm.isPending && <Spinner className="mr-2 h-4 w-4" />}
                    {t("confirm")}
                  </Button>
                </div>
                {confirmError && (
                  <p
                    className="text-destructive text-sm"
                    role="alert"
                    data-testid="mfa-confirm-error"
                  >
                    {confirmError}
                  </p>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
