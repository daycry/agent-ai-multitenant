"use client";

/**
 * Configuración del repositorio Git del proyecto (ADR 0072). Remoto + rama +
 * credenciales (PAT/SSH). El secreto se guarda en Vault (PUT /projects/{id}/git)
 * y NUNCA se devuelve; al guardar se encola el clone. Dejar la credencial vacía
 * conserva la ya guardada.
 *
 * i18n (prod-16 `task_prod16_03`): el catálogo de alineación de la rama guarda
 * la CLAVE del namespace `projectGit`, no el texto. Es el aviso que explica un
 * PR fallido por «no history in common», y era de lo peor que quedaba sin
 * traducir: se lee justo cuando algo se ha roto.
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, GitBranch, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useT, type MessageKey } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

export interface GitConfig {
  provider: string;
  remote_url: string;
  default_branch: string;
  auth_mode: string;
}

/** Resultado del último clone/sync que el worker persiste en repository_config
 *  (feedback de que la cola SÍ ejecutó + la alineación de la rama con el remoto). */
export interface LastGitSync {
  at?: string;
  status?: string; // ok | error
  default_branch_alignment?: string; // created | fast_forwarded | up_to_date | remote_empty | diverged
  error?: string;
}

const ALIGNMENT_COPY: Record<string, { warn: boolean; key: MessageKey<"projectGit"> }> = {
  created: { warn: false, key: "alignmentCreated" },
  fast_forwarded: { warn: false, key: "alignmentFastForwarded" },
  up_to_date: { warn: false, key: "alignmentUpToDate" },
  remote_empty: { warn: true, key: "alignmentRemoteEmpty" },
  diverged: { warn: true, key: "alignmentDiverged" },
};

/** Políticas del flujo git del plan (worker_config.git_policies, ADR 0072 fase 2). */
export interface GitPolicies {
  branch_push_mode: string;
  plan_validation_mode: string;
  push_policy: string;
}

export function GitConfigSection({
  projectId,
  value,
  policies,
  lastSync,
  isReadOnly = false,
}: {
  projectId: string;
  value: GitConfig | null;
  policies?: GitPolicies | null;
  lastSync?: LastGitSync | null;
  isReadOnly?: boolean;
}) {
  const queryClient = useQueryClient();
  const t = useT("projectGit");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const [provider, setProvider] = useState(value?.provider ?? "generic");
  const [remoteUrl, setRemoteUrl] = useState(value?.remote_url ?? "");
  const [branch, setBranch] = useState(value?.default_branch ?? "main");
  const [authMode, setAuthMode] = useState(value?.auth_mode ?? "none");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [sshKey, setSshKey] = useState("");
  // Políticas del flujo del plan (defaults razonables si el proyecto no las fijó).
  const [branchPushMode, setBranchPushMode] = useState(policies?.branch_push_mode ?? "incremental");
  const [planValidationMode, setPlanValidationMode] = useState(
    policies?.plan_validation_mode ?? "human_required",
  );
  const [pushPolicy, setPushPolicy] = useState(policies?.push_policy ?? "branch_only_pr_required");

  useEffect(() => {
    setProvider(value?.provider ?? "generic");
    setRemoteUrl(value?.remote_url ?? "");
    setBranch(value?.default_branch ?? "main");
    setAuthMode(value?.auth_mode ?? "none");
  }, [value?.provider, value?.remote_url, value?.default_branch, value?.auth_mode]);

  useEffect(() => {
    setBranchPushMode(policies?.branch_push_mode ?? "incremental");
    setPlanValidationMode(policies?.plan_validation_mode ?? "human_required");
    setPushPolicy(policies?.push_policy ?? "branch_only_pr_required");
  }, [policies?.branch_push_mode, policies?.plan_validation_mode, policies?.push_policy]);

  const save = useMutation<unknown, ApiError>({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/git`, {
        method: "PUT",
        body: {
          provider,
          remote_url: remoteUrl.trim(),
          default_branch: branch.trim() || "main",
          auth_mode: authMode,
          branch_push_mode: branchPushMode,
          plan_validation_mode: planValidationMode,
          push_policy: pushPolicy,
          ...(authMode === "pat" && token ? { username: username || null, token } : {}),
          ...(authMode === "ssh" && sshKey ? { ssh_key: sshKey } : {}),
        },
      }),
    onSuccess: () => {
      setToken("");
      setSshKey("");
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
    },
  });

  // Botón «Sincronizar»: re-encola el clone/fetch del remoto (el operador no
  // sabía si la cola ejecutaba; ahora el resultado se ve abajo tras unos segundos).
  const sync = useMutation<{ status: string }, ApiError>({
    mutationFn: () => apiFetch(`/projects/${projectId}/git/sync`, { method: "POST" }),
    onSuccess: () => {
      // Refresca el proyecto un par de veces para captar el last_git_sync que
      // el worker escribe al terminar (el clone tarda unos segundos).
      const refresh = () =>
        void queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
      refresh();
      setTimeout(refresh, 2500);
      setTimeout(refresh, 6000);
    },
  });

  const alignment = lastSync?.default_branch_alignment
    ? ALIGNMENT_COPY[lastSync.default_branch_alignment]
    : undefined;

  return (
    <Card className="space-y-3 p-4" data-testid="project-git-section">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <GitBranch className="h-4 w-4" />
          {t("title")}
        </h3>
        <p className="text-muted-foreground mt-1 text-xs">{t("description")}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="git-provider">{t("providerLabel")}</Label>
          <Select
            id="git-provider"
            value={provider}
            disabled={isReadOnly}
            onChange={(e) => setProvider(e.target.value)}
            data-testid="git-provider"
          >
            {/* Los tres primeros son nombres propios: no se traducen. */}
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="azure_devops">Azure DevOps</option>
            <option value="generic">{t("providerGeneric")}</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="git-branch">{t("branchLabel")}</Label>
          <Input
            id="git-branch"
            value={branch}
            disabled={isReadOnly}
            onChange={(e) => setBranch(e.target.value)}
            data-testid="git-branch"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="git-remote-url">{t("remoteUrlLabel")}</Label>
        <Input
          id="git-remote-url"
          value={remoteUrl}
          disabled={isReadOnly}
          onChange={(e) => setRemoteUrl(e.target.value)}
          placeholder="https://github.com/owner/repo.git  ·  git@host:owner/repo.git"
          data-testid="git-remote-url"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="git-auth-mode">{t("authModeLabel")}</Label>
        <Select
          id="git-auth-mode"
          value={authMode}
          disabled={isReadOnly}
          onChange={(e) => setAuthMode(e.target.value)}
          data-testid="git-auth-mode"
        >
          <option value="none">{t("authNone")}</option>
          <option value="pat">{t("authPat")}</option>
          <option value="ssh">{t("authSsh")}</option>
        </Select>
      </div>

      {authMode === "pat" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="git-username">{t("usernameLabel")}</Label>
            <Input
              id="git-username"
              value={username}
              disabled={isReadOnly}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="x-access-token (GitHub)"
              data-testid="git-username"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git-token">{t("tokenLabel")}</Label>
            <Input
              id="git-token"
              type="password"
              value={token}
              disabled={isReadOnly}
              onChange={(e) => setToken(e.target.value)}
              placeholder={t("tokenPlaceholder")}
              data-testid="git-token"
            />
          </div>
        </div>
      )}

      {authMode === "ssh" && (
        <div className="space-y-1.5">
          <Label htmlFor="git-ssh-key">{t("sshKeyLabel")}</Label>
          <textarea
            id="git-ssh-key"
            value={sshKey}
            disabled={isReadOnly}
            onChange={(e) => setSshKey(e.target.value)}
            rows={4}
            className="border-input bg-background w-full rounded-md border px-3 py-2 font-mono text-xs"
            placeholder={t("sshKeyPlaceholder")}
            data-testid="git-ssh-key"
          />
        </div>
      )}

      <div className="border-t pt-3">
        <h4 className="text-sm font-medium">{t("flowHeading")}</h4>
        <p className="text-muted-foreground mt-1 text-xs">{t("flowDescription")}</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="git-branch-push-mode">{t("branchPushLabel")}</Label>
            <Select
              id="git-branch-push-mode"
              value={branchPushMode}
              disabled={isReadOnly}
              onChange={(e) => setBranchPushMode(e.target.value)}
              data-testid="git-branch-push-mode"
            >
              <option value="incremental">{t("branchPushIncremental")}</option>
              <option value="final_only">{t("branchPushFinal")}</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git-plan-validation-mode">{t("planValidationLabel")}</Label>
            <Select
              id="git-plan-validation-mode"
              value={planValidationMode}
              disabled={isReadOnly}
              onChange={(e) => setPlanValidationMode(e.target.value)}
              data-testid="git-plan-validation-mode"
            >
              <option value="human_required">{t("planValidationHuman")}</option>
              <option value="auto_approve">{t("planValidationAuto")}</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git-push-policy">{t("pushPolicyLabel")}</Label>
            <Select
              id="git-push-policy"
              value={pushPolicy}
              disabled={isReadOnly}
              onChange={(e) => setPushPolicy(e.target.value)}
              data-testid="git-push-policy"
            >
              <option value="forbidden">{t("pushPolicyForbidden")}</option>
              <option value="branch_only_pr_required">{t("pushPolicyPr")}</option>
              {/* "Merge directo a la rama base" (direct_to_default_allowed) retirado
                  (cadena-pr T5 / P4, auditoría 2026-07-03): apply_push_policy no tiene
                  caller de producción, así que la opción se comportaba IDÉNTICA a "Abrir
                  PR". El merge directo real (fast-forward del default branch) es una
                  decisión de producto → ADR 0098 (gated). */}
            </Select>
          </div>
        </div>
      </div>

      {save.isError && (
        <p className="text-destructive text-sm" data-testid="git-error">
          {errorText(save.error)}
        </p>
      )}
      {save.isSuccess && (
        <p className="text-sm text-emerald-600" role="status">
          {t("saveOk")}
        </p>
      )}
      {sync.isSuccess && !save.isSuccess && (
        <p className="text-sm text-emerald-600" role="status">
          {t("syncQueued")}
        </p>
      )}
      {sync.isError && (
        <p className="text-destructive text-sm" data-testid="git-sync-error">
          {errorText(sync.error)}
        </p>
      )}

      {/* Estado del último clone/sync — feedback de que la cola SÍ ejecutó, más
          la alineación de la rama con el remoto (diverged explica el PR fallido). */}
      {lastSync?.at ? (
        <div className="bg-muted/30 space-y-1 rounded-md border p-3" data-testid="git-last-sync">
          <p className="text-xs">
            <span className="text-muted-foreground">{t("lastSyncLabel")}</span>{" "}
            {new Date(lastSync.at).toLocaleString(tCommon("dateLocale"))} ·{" "}
            <span className={lastSync.status === "ok" ? "text-emerald-600" : "text-destructive"}>
              {lastSync.status === "ok" ? t("lastSyncOk") : t("lastSyncFailed")}
            </span>
          </p>
          {lastSync.error ? <p className="text-destructive text-xs">{lastSync.error}</p> : null}
          {alignment ? (
            <p
              className={
                alignment.warn
                  ? "text-warning-soft-foreground flex items-start gap-1.5 text-xs"
                  : "text-muted-foreground text-xs"
              }
              data-testid="git-alignment"
            >
              {alignment.warn ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : null}
              {t(alignment.key)}
            </p>
          ) : null}
        </div>
      ) : null}

      {!isReadOnly && (
        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            disabled={!value?.remote_url || sync.isPending || save.isPending}
            onClick={() => sync.mutate()}
            data-testid="git-sync"
          >
            <RefreshCw className={`mr-1.5 h-4 w-4 ${sync.isPending ? "animate-spin" : ""}`} />
            {sync.isPending ? t("syncing") : t("sync")}
          </Button>
          <Button
            disabled={!remoteUrl.trim() || save.isPending}
            onClick={() => save.mutate()}
            data-testid="git-save"
          >
            {save.isPending ? t("saving") : t("save")}
          </Button>
        </div>
      )}
    </Card>
  );
}
