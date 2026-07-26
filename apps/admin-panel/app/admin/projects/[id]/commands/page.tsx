"use client";

/**
 * Plan 06.16 task_06_16_04 — Comandos & runtime del proyecto.
 *
 * Configura, de forma amigable (prioridad UX del operador), los dos
 * campos del proyecto que hacen que los agentes puedan lanzar comandos
 * del stack:
 *
 *   - `allowed_commands` — la allowlist deny-by-default de `shell_exec`.
 *     Solo los binarios listados pueden ejecutarse (lista vacía = nada).
 *     Se muestran como **chips** quitables + input para añadir + botones
 *     de **preset por stack** (PHP / Node / .NET / Python) que rellenan
 *     los chips de un golpe.
 *   - `default_runtime_template` — el runtime template por defecto del
 *     proyecto (php-phpunit, node-jest, …). Los `run_*` lo respetan con
 *     fallback al default por-tool cuando está vacío.
 *
 * RBAC: la edición va envuelta en <RoleGuard min="tenant_admin">; un
 * miembro sin rol admin ve los chips + el runtime en modo lectura (sin
 * botones de quitar / añadir / preset / guardar).
 *
 * Persiste vía PUT /projects/{id} (campos `allowed_commands` +
 * `default_runtime_template`).
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Terminal, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import { runtimeLabel, useRuntimeTemplates } from "@/lib/runtime-templates";

// --------------------------------------------------------------------------
// Types (mirror api_server.schemas.projects)
// --------------------------------------------------------------------------
interface Project {
  id: string;
  name: string;
  allowed_commands: string[];
  default_runtime_template: string | null;
  // P1-03: FQDN allowlist de las tools HTTP del agente (deny-by-default).
  allowed_domains: string[];
}

interface ProjectUpdate {
  allowed_commands?: string[];
  default_runtime_template?: string | null;
  allowed_domains?: string[];
}

// Stack presets — pressing one fills the chips with the typical binaries
// for that stack (merging, never clobbering, what's already there).
//
// G6a (plan guardas-research): el preset «Lectura» existe por un fallo medido.
// Sin `sed` en la lista, un agente que quería mirar 50 líneas de un fichero
// recibía «command not allowed: sed» y caía en releer el fichero entero una y
// otra vez — la read-churn que disparaba las guardas de esterilidad y bloqueó
// la tarea «Auditar dependencias». Son utilidades de TEXTO: el agente ya puede
// escribir en el worktree con `write_file`, así que no amplían la superficie.
//
// Es un preset y NO una base implícita siempre activa a propósito: la
// allowlist es deny-by-default por diseño (principio 2), y conceder siete
// binarios en silencio a todo proyecto —incluidos los que el operador cerró a
// conciencia— es una decisión suya, no de la plataforma. Un clic.
const STACK_PRESETS: { key: string; label: string; commands: string[] }[] = [
  { key: "php", label: "PHP", commands: ["php", "composer", "vendor/bin/phpunit", "pest"] },
  { key: "node", label: "Node", commands: ["npm", "npx", "node"] },
  { key: "dotnet", label: ".NET", commands: ["dotnet"] },
  { key: "python", label: "Python", commands: ["python", "pytest"] },
  {
    key: "read",
    label: "Lectura",
    commands: ["sed", "awk", "sort", "uniq", "cut", "tr", "head", "tail", "grep", "wc"],
  },
];

// The runtime templates the operator can pick come from GET /runtime-templates
// (task_06_18_08) — the backend is the single source of truth. We no longer
// hardcode the catalog here (it used to drift: 14 ids here vs 12 in dep-cache).
// "" = sin runtime por defecto (los run_* caen al default por-tool —
// backward-compatible).

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectCommandsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const projectQuery = useQuery<Project, ApiError>({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/projects/${projectId}`),
    enabled: Boolean(projectId),
    refetchOnWindowFocus: false,
  });

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-commands-page"
    >
      <ProjectBreadcrumb projectId={projectId} current="Comandos & runtime" />
      <PageHeader
        icon={<Terminal className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Comandos & runtime"
        description="Autoriza qué comandos del stack pueden lanzar los agentes y elige el runtime de ejecución."
      />

      <StateBlock
        className="mt-6"
        isLoading={projectQuery.isLoading}
        loadingSkeleton
        skeletonRows={3}
        loadingTestId="project-commands-loading"
        isError={projectQuery.isError}
        error={projectQuery.error}
        errorTitle="No se pudo cargar la configuración del proyecto"
        errorTestId="project-commands-error"
      >
        {projectQuery.data ? <CommandsEditor project={projectQuery.data} /> : null}
      </StateBlock>
    </div>
  );
}

// --------------------------------------------------------------------------
// Editor — chips + presets + runtime selector + save
// --------------------------------------------------------------------------
function CommandsEditor({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const { lang } = useLang();
  const runtimesQuery = useRuntimeTemplates();
  const runtimeTemplates = useMemo(() => runtimesQuery.data ?? [], [runtimesQuery.data]);

  const [commands, setCommands] = useState<string[]>(project.allowed_commands);
  const [runtime, setRuntime] = useState<string>(project.default_runtime_template ?? "");
  const [domains, setDomains] = useState<string[]>(project.allowed_domains ?? []);
  const [draft, setDraft] = useState("");
  const [domainDraft, setDomainDraft] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Re-seed local state if the project query refetches with new server
  // values (e.g. after another tab saved, or our own save invalidated).
  useEffect(() => {
    setCommands(project.allowed_commands);
    setRuntime(project.default_runtime_template ?? "");
    setDomains(project.allowed_domains ?? []);
  }, [project.allowed_commands, project.default_runtime_template, project.allowed_domains]);

  const mutation = useMutation<Project, ApiError, ProjectUpdate>({
    mutationFn: (payload) =>
      apiFetch<Project>(`/projects/${project.id}`, { method: "PUT", body: payload }),
    onSuccess: (updated) => {
      // Keep the cached project (used by breadcrumb + hub) fresh.
      queryClient.setQueryData<Project>(["project", project.id], (prev) =>
        prev ? { ...prev, ...updated } : updated,
      );
      void queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      setSavedAt(Date.now());
    },
  });

  // Read-only display: resolve the persisted slug to its served bilingual
  // label; fall back to the slug only if the catalog has not loaded yet.
  const runtimeDisplay = useMemo(() => {
    const found = runtimeTemplates.find((rt) => rt.id === runtime);
    return found ? runtimeLabel(found, lang) : runtime;
  }, [runtimeTemplates, runtime, lang]);

  const dirty = useMemo(() => {
    const sameCommands =
      commands.length === project.allowed_commands.length &&
      commands.every((c, i) => c === project.allowed_commands[i]);
    const sameRuntime = (runtime || null) === (project.default_runtime_template ?? null);
    const serverDomains = project.allowed_domains ?? [];
    const sameDomains =
      domains.length === serverDomains.length && domains.every((d, i) => d === serverDomains[i]);
    return !sameCommands || !sameRuntime || !sameDomains;
  }, [
    commands,
    runtime,
    domains,
    project.allowed_commands,
    project.default_runtime_template,
    project.allowed_domains,
  ]);

  function addCommand(raw: string) {
    const cmd = raw.trim();
    if (!cmd) return;
    setCommands((prev) => (prev.includes(cmd) ? prev : [...prev, cmd]));
    setSavedAt(null);
  }

  function removeCommand(cmd: string) {
    setCommands((prev) => prev.filter((c) => c !== cmd));
    setSavedAt(null);
  }

  function applyPreset(presetCommands: string[]) {
    // Merge: keep what's there, append any preset command not yet present
    // (order-preserving, no duplicates).
    setCommands((prev) => {
      const next = [...prev];
      for (const c of presetCommands) if (!next.includes(c)) next.push(c);
      return next;
    });
    setSavedAt(null);
  }

  function handleAddFromDraft() {
    addCommand(draft);
    setDraft("");
  }

  function addDomain(raw: string) {
    const domain = raw.trim().toLowerCase();
    if (!domain) return;
    setDomains((prev) => (prev.includes(domain) ? prev : [...prev, domain]));
    setSavedAt(null);
  }

  function removeDomain(domain: string) {
    setDomains((prev) => prev.filter((d) => d !== domain));
    setSavedAt(null);
  }

  function handleSave() {
    mutation.mutate({
      allowed_commands: commands,
      default_runtime_template: runtime || null,
      allowed_domains: domains,
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {/* ---- allowed_commands ---- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Comandos autorizados
            <Badge variant="warning" data-testid="commands-privileged-badge">
              Privilegiada
            </Badge>
          </CardTitle>
          <p className="text-muted-foreground text-sm" data-testid="commands-deny-by-default-hint">
            <strong>Deny-by-default</strong>: <code>shell_exec</code> solo ejecuta los binarios de
            esta lista. Una lista vacía significa que no puede ejecutar nada. Usa los presets por
            stack o añade comandos uno a uno.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Presets — admin only */}
          <RoleGuard min="tenant_admin">
            <div className="space-y-1.5">
              <Label>Presets por stack</Label>
              <div className="flex flex-wrap gap-2" data-testid="commands-presets">
                {STACK_PRESETS.map((preset) => (
                  <Button
                    key={preset.key}
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => applyPreset(preset.commands)}
                    data-testid={`commands-preset-${preset.key}`}
                    title={preset.commands.join(", ")}
                  >
                    {preset.label}
                  </Button>
                ))}
              </div>
            </div>
          </RoleGuard>

          {/* Chips */}
          <div className="space-y-1.5">
            <Label>Allowlist</Label>
            {commands.length === 0 ? (
              <p className="text-muted-foreground text-sm italic" data-testid="commands-empty">
                Sin comandos autorizados. <code>shell_exec</code> no podrá ejecutar nada hasta que
                añadas alguno.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-2" data-testid="commands-chips">
                {commands.map((cmd) => (
                  <li
                    key={cmd}
                    className="bg-muted text-foreground inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm"
                    data-testid={`command-chip-${cmd}`}
                  >
                    <code className="font-mono text-xs">{cmd}</code>
                    <RoleGuard min="tenant_admin">
                      <button
                        type="button"
                        onClick={() => removeCommand(cmd)}
                        className="text-muted-foreground hover:text-destructive rounded-full transition-colors"
                        aria-label={`Quitar ${cmd}`}
                        data-testid={`command-chip-remove-${cmd}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </RoleGuard>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Add input — admin only */}
          <RoleGuard min="tenant_admin">
            <div className="space-y-1.5">
              <Label htmlFor="commands-add-input">Añadir comando</Label>
              <div className="flex gap-2">
                <Input
                  id="commands-add-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddFromDraft();
                    }
                  }}
                  placeholder="p. ej. composer"
                  data-testid="commands-add-input"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleAddFromDraft}
                  disabled={!draft.trim()}
                  data-testid="commands-add-button"
                >
                  <Plus className="mr-1 h-4 w-4" />
                  Añadir
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                Usa el basename del binario (<code>php</code>, <code>composer</code>) o una ruta
                relativa al workspace (<code>vendor/bin/phpunit</code>).
              </p>
            </div>
          </RoleGuard>
        </CardContent>
      </Card>

      {/* ---- allowed_domains (P1-03) ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Dominios de red autorizados</CardTitle>
          <p className="text-muted-foreground text-sm" data-testid="domains-deny-by-default-hint">
            <strong>Deny-by-default</strong>: las tools HTTP del agente (<code>http_request</code>,
            descargas) solo alcanzan estos FQDN. Una lista vacía significa que el agente no puede
            salir a la red.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label>Allowlist de dominios</Label>
            {domains.length === 0 ? (
              <p className="text-muted-foreground text-sm italic" data-testid="domains-empty">
                Sin dominios autorizados: las tools HTTP no pueden salir a la red.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-2" data-testid="domains-chips">
                {domains.map((domain) => (
                  <li
                    key={domain}
                    className="bg-muted text-foreground inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm"
                    data-testid={`domain-chip-${domain}`}
                  >
                    <code className="font-mono text-xs">{domain}</code>
                    <RoleGuard min="tenant_admin">
                      <button
                        type="button"
                        onClick={() => removeDomain(domain)}
                        className="text-muted-foreground hover:text-destructive rounded-full transition-colors"
                        aria-label={`Quitar ${domain}`}
                        data-testid={`domain-chip-remove-${domain}`}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </RoleGuard>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <RoleGuard min="tenant_admin">
            <div className="space-y-1.5">
              <Label htmlFor="domains-add-input">Añadir dominio</Label>
              <div className="flex gap-2">
                <Input
                  id="domains-add-input"
                  value={domainDraft}
                  onChange={(e) => setDomainDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addDomain(domainDraft);
                      setDomainDraft("");
                    }
                  }}
                  placeholder="p. ej. api.github.com"
                  data-testid="domains-add-input"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    addDomain(domainDraft);
                    setDomainDraft("");
                  }}
                  disabled={!domainDraft.trim()}
                  data-testid="domains-add-button"
                >
                  <Plus className="mr-1 h-4 w-4" />
                  Añadir
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                FQDN exacto (<code>api.github.com</code>), sin esquema ni ruta.
              </p>
            </div>
          </RoleGuard>
        </CardContent>
      </Card>

      {/* ---- default_runtime_template ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Runtime por defecto</CardTitle>
          <p className="text-muted-foreground text-sm">
            El runtime template en el que se ejecutan los <code>run_*</code> (tests, lint, build…).
            Déjalo <em>vacío</em> para usar el runtime por defecto de cada tool
            (backward-compatible).
          </p>
        </CardHeader>
        <CardContent>
          <div className="space-y-1.5">
            <Label htmlFor="commands-runtime">Runtime template</Label>
            <RoleGuard
              min="tenant_admin"
              fallback={
                <p className="text-sm" data-testid="commands-runtime-readonly">
                  {runtime ? (
                    <span>{runtimeDisplay}</span>
                  ) : (
                    <span className="text-muted-foreground italic">
                      Sin runtime por defecto (defaults por-tool)
                    </span>
                  )}
                </p>
              }
            >
              <div className="max-w-sm">
                <Select
                  id="commands-runtime"
                  value={runtime}
                  disabled={runtimesQuery.isLoading}
                  onChange={(e) => {
                    setRuntime(e.target.value);
                    setSavedAt(null);
                  }}
                  data-testid="commands-runtime-select"
                >
                  <option value="">— Sin runtime por defecto (defaults por-tool) —</option>
                  {runtimeTemplates.map((rt) => (
                    <option key={rt.id} value={rt.id}>
                      {runtimeLabel(rt, lang)}
                    </option>
                  ))}
                </Select>
                {runtimesQuery.isError && (
                  <p
                    className="text-danger-soft-foreground mt-1.5 text-xs"
                    data-testid="commands-runtime-error"
                  >
                    No se pudo cargar el catálogo de runtimes.
                  </p>
                )}
              </div>
            </RoleGuard>
          </div>
        </CardContent>
      </Card>

      {/* ---- Save (admin only) ---- */}
      <RoleGuard min="tenant_admin">
        <div className="flex items-center gap-3" data-testid="commands-save-row">
          <Button
            type="button"
            onClick={handleSave}
            disabled={!dirty || mutation.isPending}
            data-testid="commands-save-button"
          >
            {mutation.isPending ? "Guardando…" : "Guardar cambios"}
          </Button>
          {!mutation.isPending && savedAt !== null && !dirty && (
            <span
              className="text-success-soft-foreground inline-flex items-center gap-1 text-sm"
              data-testid="commands-saved"
            >
              <Check className="h-4 w-4" />
              Guardado
            </span>
          )}
          {mutation.isError && (
            <span className="text-danger-soft-foreground text-sm" data-testid="commands-save-error">
              {mutation.error?.body ?? mutation.error?.message ?? "Error al guardar"}
            </span>
          )}
        </div>
      </RoleGuard>
    </div>
  );
}
