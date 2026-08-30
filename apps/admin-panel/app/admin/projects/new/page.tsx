"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, FilePlus2, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { runtimeLabel, useRuntimeTemplates } from "@/lib/runtime-templates";
import { useErrorText } from "@/lib/use-error-text";
import type { DeploymentDraft } from "@/components/marketplace/deployment-types";

import {
  CapabilitiesStep,
  capabilitiesBlocked,
  deployCapabilities,
  useTenantCapabilities,
  type CapabilityDeployResult,
} from "./capabilities-step";

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  team_id: string | null;
  // NOTE (Plan 06.17 task_06_17_14): `mcp_servers` / `rag_knowledge_bases`
  // are orphan placeholders on the project row (their real tables live
  // elsewhere). The wizard no longer reads or forwards them — KB grants
  // come from the template's `default_kb_grants` via the backend.
  worker_config: Record<string, unknown>;
  repository_config: Record<string, unknown> | null;
  human_approval_policy: Record<string, unknown> | null;
  is_template: boolean;
  // H1 (recorrido E2E 2026-08-29): el runtime que la plantilla DECLARA. El
  // asistente no lo leía, así que un proyecto de la plantilla CodeIgniter 4
  // (`php-phpunit`) nacía en «sin runtime por defecto» — que no es sin runtime,
  // es `DEFAULT_RUN_RUNTIME_ID` = `python-pytest`. `composer` en una imagen de
  // Python, y el `command not found` acusando al repositorio del usuario.
  default_runtime_template: string | null;
}

interface Team {
  id: string;
  name: string;
  // Marcador de equipo de PLATAFORMA: `is_builtin` implica tenant `Platform`
  // (los seeds son los únicos que lo ponen; `fork_team_into` y `create_team`
  // siempre crean con `false`). Ver el aviso de H6 más abajo.
  is_builtin?: boolean;
}

const STRIP_PREFIX = /^Plantilla:\s*/i;

function suggestedName(template: Project): string {
  return template.name.replace(STRIP_PREFIX, "").trim();
}

export default function NewProjectWizardPage() {
  const errorText = useErrorText();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { lang } = useLang();
  const tWizard = useT("projectWizard");
  const tDeploy = useT("marketplaceDeploy");
  // Los rótulos «equipos de plataforma / de este tenant» los estrena el diálogo
  // de edición del proyecto (H9a) y se reusan aquí a propósito: duplicarlos en
  // `projectWizard` sería dos textos que dicen lo mismo y que un día divergen.
  const tProjectHub = useT("projectHub");

  // Step 1: pick a template (or start blank). Step 2: customize. Step 3
  // (ADR 0142, sólo si el tenant tiene algo instalado): «Capacidades».
  // `selected === null` while in step 2 means a blank project ("proyecto en
  // blanco") — no template_id is sent and nothing is auto-granted.
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selected, setSelected] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // Plan 06.17 task_06_17_14: when a template is chosen, the operator decides
  // whether its default_kb_grants are actually applied. Default true (the
  // template's KBs are the point of picking it); a blank project ignores it.
  const [applyKbGrants, setApplyKbGrants] = useState(true);
  // Ola C / ADR 0068: forkear el equipo de la plantilla a una copia editable del
  // proyecto. Arranca en `false` porque es el estado del proyecto EN BLANCO, que
  // no tiene equipo de plantilla que copiar; `pickTemplate` lo pone a `true`.
  const [forkTeam, setForkTeam] = useState(false);
  // Equipo para un proyecto EN BLANCO (sin plantilla): "" = sin equipo. En los
  // basados en plantilla el equipo lo aporta la plantilla (selected.team_id).
  const [teamId, setTeamId] = useState("");
  // The stack's default runtime template (06.18 GET /runtime-templates). "" =
  // no default (the run_* tools fall back to per-tool defaults).
  const [runtime, setRuntime] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ADR 0142 (D3): lo que el proyecto recibe al nacer. `drafts` va por
  // `installation_id` y sólo tiene entrada para lo MARCADO.
  const capabilities = useTenantCapabilities();
  const [drafts, setDrafts] = useState<Record<string, DeploymentDraft>>({});
  const [deployResults, setDeployResults] = useState<CapabilityDeployResult[] | null>(null);
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);
  const totalSteps = capabilities.length > 0 ? 3 : 2;

  const templatesQuery = useQuery({
    queryKey: ["projects", "templates"],
    queryFn: () =>
      apiFetch<Project[]>("/projects?include_templates=true").then((rows) =>
        rows.filter((p) => p.is_template),
      ),
    refetchOnWindowFocus: false,
  });

  const teamsQuery = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<Team[]>("/teams"),
    refetchOnWindowFocus: false,
  });

  const runtimesQuery = useRuntimeTemplates();
  const runtimeTemplates = runtimesQuery.data ?? [];

  // ---- El runtime de la plantilla que el catálogo no sirve ----------------
  //
  // `pickTemplate` fija `template.default_runtime_template` SIN mirar el
  // catálogo. Si ese id no está entre las opciones, el `<select>` se queda sin
  // nada seleccionado: el operador ve un desplegable en blanco —indistinguible
  // de «— Sin runtime por defecto —»— mientras el formulario envía un id que
  // nadie ha visto. Y «sin runtime» NO es sin runtime: es
  // `DEFAULT_RUN_RUNTIME_ID` = `python-pytest` (H1).
  //
  // Por eso el valor se CONSERVA y se pinta con su propia opción. Caer a `""`
  // por nuestra cuenta sería elegir por el operador justo el valor peligroso.
  const runtimeOrphan = runtime !== "" && !runtimeTemplates.some((rt) => rt.id === runtime);
  // Y sólo se ACUSA al catálogo de no servirlo cuando el catálogo ha
  // contestado. Mientras carga o si falla no sabemos qué sirve, y no saberlo no
  // es saber que no está — la regla del ADR 0162: un valor ausente no puede
  // significar nada más fuerte que «desconocido».
  const runtimeUnknown = runtimeOrphan && runtimesQuery.isSuccess;

  const teamsById = new Map((teamsQuery.data ?? []).map((t) => [t.id, t]));

  // ---- H6: el equipo de la plantilla, y si es utilizable por este tenant ----
  //
  // Un equipo `is_builtin` es del tenant `Platform`, y sus agentes también. Las
  // DOS vías que resuelven agentes desde el equipo del proyecto filtran por el
  // tenant del PROYECTO y no hacen excepción con la plataforma:
  //
  //   * chat    — `chat.responder.team_role_agents`: `Team.tenant_id` Y
  //               `Agent.tenant_id` han de ser los del proyecto;
  //   * despacho— `orchestrator.dispatch.Dispatcher._candidates`:
  //               `Agent.tenant_id == task.tenant_id`.
  //
  // O sea: referenciar un built-in no da «un equipo compartido», da CERO agentes
  // utilizables — peor que no tener equipo, porque sin equipo el pool de
  // despacho todavía incluye los globales del tenant. Por eso aquí la copia no
  // es una preferencia: es la única forma de que el proyecto pueda planificar.
  const templateTeam = selected?.team_id ? teamsById.get(selected.team_id) : undefined;
  const forkTeamRequired = templateTeam?.is_builtin === true;
  const effectiveForkTeam = forkTeamRequired || forkTeam;
  // El mismo corte para el desplegable del proyecto EN BLANCO (H9a): ahí no hay
  // casilla de «personalizar», así que un built-in elegido no tendría arreglo.
  const allTeams = teamsQuery.data ?? [];
  const platformTeams = allTeams.filter((t) => t.is_builtin === true);
  const tenantTeams = allTeams.filter((t) => t.is_builtin !== true);

  function pickTemplate(template: Project) {
    setSelected(template);
    setName(suggestedName(template));
    setDescription(template.description ?? "");
    setApplyKbGrants(true);
    // H1: el runtime que declara la plantilla, preseleccionado y EDITABLE — «sin
    // runtime» sigue siendo elegible para quien lo quiera a propósito.
    setRuntime(template.default_runtime_template ?? "");
    // H6: copiar el equipo es el default al adoptar plantilla, igual que en el
    // servidor (`_resolve_template_adoption`: `fork_team = template is not
    // None`). El asistente mandaba `fork_team: false` explícito y ese `false`
    // entraba en `model_fields_set`, así que la herencia del servidor no llegaba
    // a correr NUNCA: los dos hallazgos nacen del mismo envío de más.
    setForkTeam(true);
    setStep(2);
    setSubmitError(null);
  }

  function startBlank() {
    setSelected(null);
    setName("");
    setDescription("");
    setApplyKbGrants(true);
    setRuntime("");
    setForkTeam(false);
    setStep(2);
    setSubmitError(null);
  }

  const createProject = useMutation({
    mutationFn: async () => {
      // A blank project sends no template_id (and grants nothing). A
      // template-backed one sends template_id + the apply_template_kb_grants
      // flag so the backend (routers/projects.py) decides on the grants.
      const body: Record<string, unknown> = {
        name,
        description: description || null,
        default_runtime_template: runtime || null,
      };
      if (selected) {
        body.template_id = selected.id;
        body.apply_template_kb_grants = applyKbGrants;
        body.team_id = selected.team_id;
        if (selected.team_id) body.fork_team = effectiveForkTeam;
        body.worker_config = selected.worker_config;
        body.repository_config = selected.repository_config;
        body.human_approval_policy = selected.human_approval_policy;
      } else {
        // Proyecto en blanco: el equipo lo elige el operador (o ninguno).
        body.team_id = teamId || null;
      }
      return apiFetch<Project>("/projects", { method: "POST", body });
    },
    onSuccess: async (created) => {
      queryClient.invalidateQueries({ queryKey: ["projects", "tenant"] });
      if (Object.keys(drafts).length === 0) {
        router.push(`/admin/projects?created=${created.id}`);
        return;
      }
      // El proyecto YA existe: los despliegues se encadenan aquí y lo que no
      // entre se enseña. Redirigir sin mirar convertiría un despliegue fallido
      // en un éxito silencioso, que es el modo de fallo que este plan cierra.
      setCreatedProjectId(created.id);
      const results = await deployCapabilities(created.id, capabilities, drafts, errorText);
      setDeployResults(results);
    },
    onError: (err: unknown) => {
      setSubmitError(errorText(err));
    },
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={
          <span data-testid="wizard-title">
            {step === 1
              ? tWizard("titleStep1")
              : step === 2
                ? tWizard("titleStep2")
                : `${tWizard("titleStep3Prefix")}${tDeploy("wizardStepTitle")}`}
          </span>
        }
        description={tWizard("stepOf", { step, total: totalSteps })}
        actions={
          <Button variant="outline" asChild>
            <Link href="/admin/projects">
              <ArrowLeft className="mr-1 h-4 w-4" /> {tWizard("cancel")}
            </Link>
          </Button>
        }
      />

      {/* ============ Step 1: pick a template ============ */}
      {step === 1 && (
        <section data-testid="wizard-step-1">
          {/* Proyecto en blanco — sin plantilla, sin auto-grants de KB. */}
          <Card
            data-testid="wizard-blank-project"
            className="mb-4 flex items-center justify-between gap-4 p-4"
            interactive
            onClick={startBlank}
          >
            <div className="flex items-center gap-3">
              <FilePlus2 className="text-muted-foreground h-6 w-6" />
              <div>
                <p className="font-semibold">{tWizard("blankTitle")}</p>
                <p className="text-muted-foreground text-sm">{tWizard("blankHint")}</p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                startBlank();
              }}
              data-testid="wizard-blank-project-pick"
            >
              {tWizard("blankStart")} <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </Card>

          {templatesQuery.isLoading && (
            <p className="text-muted-foreground text-sm">{tWizard("loadingTemplates")}</p>
          )}
          {templatesQuery.isError && (
            <Card className="border-destructive p-4">
              <p className="text-destructive text-sm">
                {tWizard("templatesError")} {errorText(templatesQuery.error)}
              </p>
            </Card>
          )}
          {templatesQuery.data && (
            <div
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
              data-testid="templates-grid"
            >
              {templatesQuery.data.map((t) => {
                const team = t.team_id ? teamsById.get(t.team_id) : null;
                return (
                  <Card
                    key={t.id}
                    data-testid={`template-${t.id}`}
                    className="flex h-full flex-col"
                    interactive
                    onClick={() => pickTemplate(t)}
                  >
                    <CardHeader className="pb-2">
                      <CardTitle className="text-base">{t.name}</CardTitle>
                      {team && (
                        <Badge variant="info" className="w-fit">
                          {team.name}
                        </Badge>
                      )}
                    </CardHeader>
                    <CardContent className="flex flex-1 flex-col justify-between gap-3">
                      {t.description && (
                        <p className="text-muted-foreground text-sm">{t.description}</p>
                      )}
                      <Button
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          pickTemplate(t);
                        }}
                        data-testid={`template-pick-${t.id}`}
                      >
                        {tWizard("useTemplate")} <ArrowRight className="ml-1 h-3.5 w-3.5" />
                      </Button>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* ============ Step 2: customize ============ */}
      {step === 2 && (
        <section data-testid="wizard-step-2" className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">{tWizard("detailsTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="name">{tWizard("nameLabel")}</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  data-testid="wizard-name"
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>{tWizard("descriptionLabel")}</Label>
                <MarkdownTextarea
                  value={description}
                  onChange={setDescription}
                  rows={4}
                  data-testid="wizard-description"
                />
              </div>

              {/* Runtime por defecto del stack (06.18 GET /runtime-templates). */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="wizard-runtime">{tWizard("runtimeLabel")}</Label>
                <Select
                  id="wizard-runtime"
                  value={runtime}
                  disabled={runtimesQuery.isLoading}
                  onChange={(e) => setRuntime(e.target.value)}
                  data-testid="wizard-runtime-select"
                >
                  <option value="">— {tWizard("runtimeNone")} —</option>
                  {/* El id que no está en el catálogo, con su propia opción: sin
                      ella el desplegable se queda en blanco y miente. */}
                  {runtimeOrphan && (
                    <option value={runtime} data-testid="wizard-runtime-orphan-option">
                      {runtime}
                    </option>
                  )}
                  {runtimeTemplates.map((rt) => (
                    <option key={rt.id} value={rt.id}>
                      {runtimeLabel(rt, lang)}
                    </option>
                  ))}
                </Select>
                {runtimeUnknown && (
                  <p
                    className="bg-warning-soft text-warning-soft-foreground rounded p-2 text-xs"
                    data-testid="wizard-runtime-unknown"
                  >
                    {tWizard("runtimeUnknown", { id: runtime })}
                  </p>
                )}
                {runtimesQuery.isError && (
                  <p
                    className="text-danger-soft-foreground text-xs"
                    data-testid="wizard-runtime-error"
                  >
                    {tWizard("runtimeError")}
                  </p>
                )}
              </div>

              {/* Proyecto EN BLANCO: elegir equipo (en los de plantilla lo aporta
                  la plantilla — se muestra abajo). ADR 0071. */}
              {!selected && (
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="wizard-team">{tWizard("teamLabel")}</Label>
                  <Select
                    id="wizard-team"
                    value={teamId}
                    onChange={(e) => setTeamId(e.target.value)}
                    data-testid="wizard-team-select"
                  >
                    <option value="">{tWizard("teamNone")}</option>
                    {tenantTeams.length > 0 && (
                      <optgroup label={tProjectHub("teamGroupTenant")}>
                        {tenantTeams.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {/* Mismo motivo que en «Editar proyecto» (H9a), y aquí más
                        grave: el proyecto en blanco no tiene casilla de
                        «personalizar el equipo», así que elegir un built-in no
                        tendría remedio desde esta pantalla. */}
                    {platformTeams.length > 0 && (
                      <optgroup label={tProjectHub("teamGroupPlatform")}>
                        {platformTeams.map((t) => (
                          <option key={t.id} value={t.id} disabled>
                            {t.name}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </Select>
                  <p className="text-muted-foreground text-xs">{tWizard("teamHint")}</p>
                </div>
              )}

              {/* KB grants de la plantilla — solo si hay plantilla elegida. */}
              {selected && (
                <label
                  className="flex cursor-pointer items-start gap-2 text-sm"
                  data-testid="wizard-apply-kb-grants-label"
                >
                  <Checkbox
                    checked={applyKbGrants}
                    onChange={(e) => setApplyKbGrants(e.target.checked)}
                    data-testid="wizard-apply-kb-grants"
                  />
                  <span>
                    {tWizard("applyKbGrants")}
                    <span className="text-muted-foreground block text-xs">
                      {tWizard("applyKbGrantsHint")}
                    </span>
                  </span>
                </label>
              )}

              {submitError && (
                <p
                  className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                  data-testid="wizard-error"
                >
                  {submitError}
                </p>
              )}
              <div className="mt-2 flex items-center justify-between">
                <Button variant="outline" onClick={() => setStep(1)} data-testid="wizard-back">
                  ← {selected ? tWizard("changeTemplate") : tWizard("back")}
                </Button>
                {/* Con capacidades instaladas el wizard gana un paso (ADR 0142
                    D3); sin ellas se crea desde aquí, como antes. */}
                {totalSteps === 3 ? (
                  <Button onClick={() => setStep(3)} disabled={!name} data-testid="wizard-next">
                    {tDeploy("next")} <ArrowRight className="ml-1 h-3.5 w-3.5" />
                  </Button>
                ) : (
                  <Button
                    onClick={() => createProject.mutate()}
                    disabled={!name || createProject.isPending}
                    data-testid="wizard-submit"
                  >
                    {createProject.isPending && <Spinner className="mr-2 h-4 w-4" />}
                    {createProject.isPending ? tWizard("creating") : tWizard("create")}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Preview pane: what the template ships with (o "en blanco"). */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{tWizard("previewTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <div>
                <span className="text-muted-foreground text-xs uppercase">
                  {tWizard("previewTemplate")}
                </span>
                <p className="font-medium" data-testid="wizard-preview-template">
                  {selected ? selected.name : tWizard("previewBlank")}
                </p>
              </div>
              {selected?.team_id && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">
                    {tWizard("teamLabel")}
                  </span>
                  <p>{teamsById.get(selected.team_id)?.name ?? selected.team_id}</p>
                </div>
              )}
              {selected?.team_id && (
                <div className="flex flex-col gap-1.5">
                  <label className="flex items-start gap-2" data-testid="wizard-fork-team">
                    <Checkbox
                      checked={effectiveForkTeam}
                      disabled={forkTeamRequired}
                      onChange={(e) => setForkTeam(e.target.checked)}
                      data-testid="wizard-fork-team-checkbox"
                    />
                    <span>
                      {tWizard("forkTeam")}
                      <span className="text-muted-foreground block text-xs">
                        {tWizard("forkTeamHint")}
                      </span>
                    </span>
                  </label>
                  {/* H6: con un equipo de plataforma la copia no es opcional —
                      desmarcarla daría un proyecto sin agentes visibles. Se
                      explica en vez de desaparecer la casilla: quien la busque
                      tiene que entender por qué está bloqueada. */}
                  {forkTeamRequired && (
                    <p
                      className="bg-warning-soft text-warning-soft-foreground rounded p-2 text-xs"
                      data-testid="wizard-fork-team-required"
                    >
                      {tWizard("forkTeamRequired")}
                    </p>
                  )}
                </div>
              )}
              {selected?.human_approval_policy?.preset != null && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">
                    {tWizard("previewPolicy")}
                  </span>
                  <p>
                    <Badge variant="warning">{String(selected.human_approval_policy.preset)}</Badge>
                  </p>
                </div>
              )}
              {selected?.repository_config && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">
                    {tWizard("previewRepository")}
                  </span>
                  <pre className="bg-muted text-muted-foreground overflow-auto rounded p-2 text-xs">
                    {JSON.stringify(selected.repository_config, null, 2)}
                  </pre>
                </div>
              )}
            </CardContent>
          </Card>
        </section>
      )}

      {/* ============ Step 3: Capacidades (ADR 0142, D3) ============ */}
      {step === 3 && (
        <section data-testid="wizard-step-3" className="space-y-4">
          <CapabilitiesStep
            capabilities={capabilities}
            drafts={drafts}
            onDraftsChange={setDrafts}
          />

          {submitError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="wizard-error"
            >
              {submitError}
            </p>
          )}

          {/* Resultado del encadenado: el proyecto YA existe, así que lo que
              importa es qué entró y qué no — no un redirect optimista. */}
          {deployResults ? (
            <Card data-testid="wizard-deploy-results">
              <CardHeader>
                <CardTitle className="text-base">{tDeploy("wizardResultsTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {deployResults.map((result) => (
                  <div key={result.installationId} className="space-y-1">
                    <p
                      data-testid={`wizard-deploy-result-${result.installationId}`}
                      data-outcome={result.outcome}
                      className={result.outcome === "failed" ? "text-destructive" : ""}
                    >
                      {result.outcome === "ok"
                        ? tDeploy("resultOk", { project: result.name })
                        : result.outcome === "already"
                          ? tDeploy("resultAlready", { project: result.name })
                          : `${tDeploy("resultFailed", { project: result.name })} ${result.error ?? ""}`}
                    </p>
                    {result.warnings.length > 0 ? (
                      <ul
                        className="text-warning-soft-foreground space-y-0.5 pl-4"
                        data-testid={`wizard-deploy-warnings-${result.installationId}`}
                      >
                        {result.warnings.map((warning, index) => (
                          <li key={index}>• {warning}</li>
                        ))}
                      </ul>
                    ) : null}
                    {result.oauthPending ? (
                      <p
                        className="text-warning-soft-foreground pl-4"
                        data-testid={`wizard-deploy-oauth-${result.installationId}`}
                      >
                        {tDeploy("oauthPending")}
                      </p>
                    ) : null}
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : null}

          <div className="flex items-center justify-between">
            <Button
              variant="outline"
              onClick={() => setStep(2)}
              disabled={createProject.isPending || deployResults !== null}
              data-testid="wizard-capabilities-back"
            >
              ← {tDeploy("back")}
            </Button>
            {deployResults && createdProjectId ? (
              <Button asChild data-testid="wizard-goto-project">
                <Link href={`/admin/projects/${createdProjectId}`}>
                  {tDeploy("wizardGoToProject")}
                </Link>
              </Button>
            ) : (
              <Button
                onClick={() => createProject.mutate()}
                disabled={
                  !name || createProject.isPending || capabilitiesBlocked(capabilities, drafts)
                }
                data-testid="wizard-submit"
              >
                {createProject.isPending && <Spinner className="mr-2 h-4 w-4" />}
                {createProject.isPending ? tWizard("creating") : tWizard("create")}
              </Button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
