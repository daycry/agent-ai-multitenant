"use client";

/**
 * Configuración del repositorio Git del proyecto (ADR 0072). Remoto + rama +
 * credenciales (PAT/SSH). El secreto se guarda en Vault (PUT /projects/{id}/git)
 * y NUNCA se devuelve; al guardar se encola el clone. Dejar la credencial vacía
 * conserva la ya guardada.
 */

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GitBranch } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";

export interface GitConfig {
  provider: string;
  remote_url: string;
  default_branch: string;
  auth_mode: string;
}

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
  isReadOnly = false,
}: {
  projectId: string;
  value: GitConfig | null;
  policies?: GitPolicies | null;
  isReadOnly?: boolean;
}) {
  const queryClient = useQueryClient();
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

  return (
    <Card className="space-y-3 p-4" data-testid="project-git-section">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <GitBranch className="h-4 w-4" />
          Repositorio Git
        </h3>
        <p className="text-muted-foreground mt-1 text-xs">
          Remoto + credenciales (PAT/SSH). El secreto se guarda en Vault y nunca se muestra; al
          guardar se encola el clone. Deja la credencial vacía para conservar la ya guardada.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="git-provider">Proveedor</Label>
          <Select
            id="git-provider"
            value={provider}
            disabled={isReadOnly}
            onChange={(e) => setProvider(e.target.value)}
            data-testid="git-provider"
          >
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="azure_devops">Azure DevOps</option>
            <option value="generic">Genérico</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="git-branch">Rama por defecto</Label>
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
        <Label htmlFor="git-remote-url">URL del remoto</Label>
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
        <Label htmlFor="git-auth-mode">Autenticación</Label>
        <Select
          id="git-auth-mode"
          value={authMode}
          disabled={isReadOnly}
          onChange={(e) => setAuthMode(e.target.value)}
          data-testid="git-auth-mode"
        >
          <option value="none">Sin auth (público / preconfigurado)</option>
          <option value="pat">PAT (HTTPS)</option>
          <option value="ssh">Clave SSH</option>
        </Select>
      </div>

      {authMode === "pat" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="git-username">Usuario (opcional)</Label>
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
            <Label htmlFor="git-token">Token (PAT)</Label>
            <Input
              id="git-token"
              type="password"
              value={token}
              disabled={isReadOnly}
              onChange={(e) => setToken(e.target.value)}
              placeholder="••• (vacío = conservar)"
              data-testid="git-token"
            />
          </div>
        </div>
      )}

      {authMode === "ssh" && (
        <div className="space-y-1.5">
          <Label htmlFor="git-ssh-key">Clave SSH privada</Label>
          <textarea
            id="git-ssh-key"
            value={sshKey}
            disabled={isReadOnly}
            onChange={(e) => setSshKey(e.target.value)}
            rows={4}
            className="border-input bg-background w-full rounded-md border px-3 py-2 font-mono text-xs"
            placeholder="(pegar clave privada; vacío = conservar la guardada)"
            data-testid="git-ssh-key"
          />
        </div>
      )}

      <div className="border-t pt-3">
        <h4 className="text-sm font-medium">Flujo git del plan</h4>
        <p className="text-muted-foreground mt-1 text-xs">
          Cómo se publican las ramas y qué pasa al cerrar el plan. Por defecto: los agentes empujan
          la rama del plan tarea a tarea, el humano valida al cerrar y se abre un PR (sin merge
          directo).
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="git-branch-push-mode">Push de la rama</Label>
            <Select
              id="git-branch-push-mode"
              value={branchPushMode}
              disabled={isReadOnly}
              onChange={(e) => setBranchPushMode(e.target.value)}
              data-testid="git-branch-push-mode"
            >
              <option value="incremental">Incremental (cada tarea)</option>
              <option value="final_only">Solo al cerrar el plan</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git-plan-validation-mode">Validación del plan</Label>
            <Select
              id="git-plan-validation-mode"
              value={planValidationMode}
              disabled={isReadOnly}
              onChange={(e) => setPlanValidationMode(e.target.value)}
              data-testid="git-plan-validation-mode"
            >
              <option value="human_required">Validación humana</option>
              <option value="auto_approve">Auto-aprobar</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="git-push-policy">Al cerrar el plan</Label>
            <Select
              id="git-push-policy"
              value={pushPolicy}
              disabled={isReadOnly}
              onChange={(e) => setPushPolicy(e.target.value)}
              data-testid="git-push-policy"
            >
              <option value="forbidden">No hacer nada</option>
              <option value="branch_only_pr_required">Abrir PR (revisión humana)</option>
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
          {save.error?.body ?? "Error al guardar"}
        </p>
      )}
      {save.isSuccess && (
        <p className="text-sm text-emerald-600" role="status">
          Guardado. Clone encolado.
        </p>
      )}

      {!isReadOnly && (
        <div className="flex justify-end">
          <Button
            disabled={!remoteUrl.trim() || save.isPending}
            onClick={() => save.mutate()}
            data-testid="git-save"
          >
            {save.isPending ? "Guardando…" : "Guardar repositorio"}
          </Button>
        </div>
      )}
    </Card>
  );
}
