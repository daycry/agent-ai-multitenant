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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import { runtimeLabel, useRuntimeTemplates } from "@/lib/runtime-templates";

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
}

interface Team {
  id: string;
  name: string;
}

const STRIP_PREFIX = /^Plantilla:\s*/i;

function suggestedName(template: Project): string {
  return template.name.replace(STRIP_PREFIX, "").trim();
}

export default function NewProjectWizardPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { lang } = useLang();

  // Step 1: pick a template (or start blank). Step 2: customize + create.
  // `selected === null` while in step 2 means a blank project ("proyecto en
  // blanco") — no template_id is sent and nothing is auto-granted.
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // Plan 06.17 task_06_17_14: when a template is chosen, the operator decides
  // whether its default_kb_grants are actually applied. Default true (the
  // template's KBs are the point of picking it); a blank project ignores it.
  const [applyKbGrants, setApplyKbGrants] = useState(true);
  // The stack's default runtime template (06.18 GET /runtime-templates). "" =
  // no default (the run_* tools fall back to per-tool defaults).
  const [runtime, setRuntime] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

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

  const teamsById = new Map((teamsQuery.data ?? []).map((t) => [t.id, t]));

  function pickTemplate(template: Project) {
    setSelected(template);
    setName(suggestedName(template));
    setDescription(template.description ?? "");
    setApplyKbGrants(true);
    setStep(2);
    setSubmitError(null);
  }

  function startBlank() {
    setSelected(null);
    setName("");
    setDescription("");
    setApplyKbGrants(true);
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
        body.worker_config = selected.worker_config;
        body.repository_config = selected.repository_config;
        body.human_approval_policy = selected.human_approval_policy;
      }
      return apiFetch<Project>("/projects", { method: "POST", body });
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["projects", "tenant"] });
      router.push(`/admin/projects?created=${created.id}`);
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.body : String(err));
    },
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={
          <span data-testid="wizard-title">
            {step === 1 ? "Crear proyecto — elige plantilla" : "Crear proyecto — personaliza"}
          </span>
        }
        description={`Paso ${step} de 2.`}
        actions={
          <Button variant="outline" asChild>
            <Link href="/admin/projects">
              <ArrowLeft className="mr-1 h-4 w-4" /> Cancelar
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
                <p className="font-semibold">Proyecto en blanco</p>
                <p className="text-muted-foreground text-sm">
                  Empieza sin plantilla. No se concede ninguna base de conocimiento por defecto.
                </p>
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
              Empezar en blanco <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </Card>

          {templatesQuery.isLoading && (
            <p className="text-muted-foreground text-sm">Cargando plantillas…</p>
          )}
          {templatesQuery.isError && (
            <Card className="border-destructive p-4">
              <p className="text-destructive text-sm">
                Could not load templates:{" "}
                {templatesQuery.error instanceof ApiError
                  ? templatesQuery.error.body
                  : String(templatesQuery.error)}
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
                        Usar plantilla <ArrowRight className="ml-1 h-3.5 w-3.5" />
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
              <CardTitle className="text-base">Detalles del proyecto</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="name">Nombre</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  data-testid="wizard-name"
                  required
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="description">Descripción</Label>
                <textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                  data-testid="wizard-description"
                />
              </div>

              {/* Runtime por defecto del stack (06.18 GET /runtime-templates). */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="wizard-runtime">Runtime por defecto</Label>
                <Select
                  id="wizard-runtime"
                  value={runtime}
                  disabled={runtimesQuery.isLoading}
                  onChange={(e) => setRuntime(e.target.value)}
                  data-testid="wizard-runtime-select"
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
                    className="text-danger-soft-foreground text-xs"
                    data-testid="wizard-runtime-error"
                  >
                    No se pudo cargar el catálogo de runtimes.
                  </p>
                )}
              </div>

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
                    Conceder las bases de conocimiento de la plantilla
                    <span className="text-muted-foreground block text-xs">
                      Si lo desmarcas, el proyecto adopta la plantilla pero no recibe ninguna KB por
                      defecto.
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
                  ← {selected ? "Cambiar plantilla" : "Volver"}
                </Button>
                <Button
                  onClick={() => createProject.mutate()}
                  disabled={!name || createProject.isPending}
                  data-testid="wizard-submit"
                >
                  {createProject.isPending && <Spinner className="mr-2 h-4 w-4" />}
                  {createProject.isPending ? "Creando…" : "Crear proyecto"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Preview pane: what the template ships with (o "en blanco"). */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Preview</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <div>
                <span className="text-muted-foreground text-xs uppercase">Plantilla</span>
                <p className="font-medium" data-testid="wizard-preview-template">
                  {selected ? selected.name : "Proyecto en blanco (sin plantilla)"}
                </p>
              </div>
              {selected?.team_id && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">Equipo</span>
                  <p>{teamsById.get(selected.team_id)?.name ?? selected.team_id}</p>
                </div>
              )}
              {selected?.human_approval_policy?.preset != null && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">Política humana</span>
                  <p>
                    <Badge variant="warning">{String(selected.human_approval_policy.preset)}</Badge>
                  </p>
                </div>
              )}
              {selected?.repository_config && (
                <div>
                  <span className="text-muted-foreground text-xs uppercase">Repositorio</span>
                  <pre className="bg-muted text-muted-foreground overflow-auto rounded p-2 text-xs">
                    {JSON.stringify(selected.repository_config, null, 2)}
                  </pre>
                </div>
              )}
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
