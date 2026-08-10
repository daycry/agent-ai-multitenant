"use client";

/**
 * task_01_23 — Configurar Política de Validación Humana.
 *
 * Single-screen flow for picking a built-in preset (Sandbox /
 * Desarrollo / Producción / Cliente Externo) and optionally overriding
 * individual categories. When a project is selected the "Guardar"
 * button copies the resulting `categories` map into that project's
 * `human_approval_policy` field.
 *
 * Without a selected project the screen still works as a *preview*:
 * the preset row selects which row to inspect and the category table
 * shows the would-be decisions. Useful for understanding what each
 * preset bundles before adopting one.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Domain
// --------------------------------------------------------------------------
type Decision = "auto" | "human_required";

interface ApprovalPolicy {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  categories: {
    /** Slug del preset sembrado (`sandbox`, `production`, …), si lo trae. */
    preset?: string;
    categories: Record<string, Decision>;
    /** ADR 0153: qué hace el gate con una categoría que la tabla no lista. */
    unlisted_category?: Decision;
  };
}

/**
 * ADR 0153 — espejo del default que aplican `api_server.db.approval_repo` y
 * `agent_runtime.approval` cuando la política no trae `unlisted_category`.
 * Se pinta para que el operador VEA qué está decidiendo antes de guardar: una
 * política que falla cerrado sin que se vea dónde se configura eso es un ticket
 * de soporte por proyecto.
 */
const UNLISTED_DEFAULT_BY_PRESET: Record<string, Decision> = {
  sandbox: "auto",
  development: "auto",
  production: "human_required",
  "customer-external": "human_required",
};

/** Sin clave y sin preset reconocible se para: fail-closed, igual que el motor. */
const UNLISTED_FALLBACK: Decision = "human_required";

function baselineUnlisted(policy: ApprovalPolicy | null): Decision {
  const explicit = policy?.categories.unlisted_category;
  if (explicit === "auto" || explicit === "human_required") return explicit;
  const preset = policy?.categories.preset;
  return (preset && UNLISTED_DEFAULT_BY_PRESET[preset]) || UNLISTED_FALLBACK;
}

interface Project {
  id: string;
  name: string;
  is_template: boolean;
  human_approval_policy: { categories?: Record<string, Decision> } | null;
}

// Stable label/order for the 13 categories (spec §7.7-7.8).
const CATEGORY_LABELS: Array<{ id: string; label: string; hint: string }> = [
  { id: "code_changes", label: "Cambios de código", hint: "Edición de ficheros" },
  { id: "git_commit", label: "Commit", hint: "git commit local" },
  { id: "git_push", label: "Push", hint: "git push remoto" },
  { id: "external_http_get", label: "HTTP GET externo", hint: "Lecturas a internet" },
  { id: "external_http_post", label: "HTTP POST externo", hint: "Escrituras a internet" },
  { id: "secrets_access", label: "Acceso a secretos", hint: "Lectura de Vault" },
  {
    id: "data_migration",
    // El hint decía solo «DDL / alembic», y desde prod-03 task_prod03_02 esta
    // categoría gatea además `promote_to_kb` (copiar un documento y sus chunks a
    // otra KB del tenant, de donde lo leerá por RAG cualquier proyecto con
    // grant). Un operador que leyera «DDL» descartaría la categoría por
    // irrelevante y dejaría en `auto` la única puerta que hay sobre la escritura
    // persistente en la base de conocimiento.
    label: "Migración de datos",
    hint: "DDL / alembic y promoción de documentos a otra KB (promote_to_kb)",
  },
  { id: "production_deploy", label: "Despliegue producción", hint: "Rolling out a prod" },
  { id: "infra_provision", label: "Aprovisionar infra", hint: "Crear recursos" },
  { id: "secret_rotation", label: "Rotación de secretos", hint: "Vault rotate" },
  {
    id: "external_communication",
    label: "Comunicación externa",
    hint: "Email / Slack hacia fuera",
  },
  { id: "data_export_pii", label: "Exportar PII", hint: "Datos personales fuera del sistema" },
  { id: "user_management", label: "Gestión de usuarios", hint: "Alta / baja / RBAC" },
];

const DECISION_BADGE: Record<Decision, { label: string; variant: "success" | "warning" }> = {
  auto: { label: "Auto", variant: "success" },
  human_required: { label: "Humano", variant: "warning" },
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ApprovalPolicyPage() {
  const queryClient = useQueryClient();

  const policiesQuery = useQuery({
    queryKey: ["approval-policies"],
    queryFn: () => apiFetch<ApprovalPolicy[]>("/approval-policies?builtin_only=true"),
    refetchOnWindowFocus: false,
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "tenant"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  const policies = policiesQuery.data ?? [];
  const projects = useMemo(
    () => (projectsQuery.data ?? []).filter((p) => !p.is_template),
    [projectsQuery.data],
  );

  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [overrides, setOverrides] = useState<Record<string, Decision>>({});
  // ADR 0153: override de `unlisted_category`. `null` = el valor del preset.
  const [unlistedOverride, setUnlistedOverride] = useState<Decision | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitOk, setSubmitOk] = useState(false);

  // Auto-select the first preset (Sandbox) once policies load.
  useEffect(() => {
    if (!selectedPolicyId && policies.length > 0) {
      setSelectedPolicyId(policies[0].id);
    }
  }, [policies, selectedPolicyId]);

  // Reset overrides when the preset changes.
  useEffect(() => {
    setOverrides({});
    setUnlistedOverride(null);
    setSubmitOk(false);
  }, [selectedPolicyId]);

  const activePolicy = policies.find((p) => p.id === selectedPolicyId) ?? null;
  const baseDecisions: Record<string, Decision> = activePolicy
    ? activePolicy.categories.categories
    : {};
  const effectiveDecisions: Record<string, Decision> = {
    ...baseDecisions,
    ...overrides,
  };
  const baseUnlisted = baselineUnlisted(activePolicy);
  const effectiveUnlisted: Decision = unlistedOverride ?? baseUnlisted;

  function toggle(category: string) {
    const current = effectiveDecisions[category] ?? "auto";
    const next: Decision = current === "auto" ? "human_required" : "auto";
    setOverrides((prev) => {
      const copy = { ...prev };
      if (baseDecisions[category] === next) {
        // back to baseline -> drop the override entry
        delete copy[category];
      } else {
        copy[category] = next;
      }
      return copy;
    });
    setSubmitOk(false);
  }

  function toggleUnlisted() {
    const next: Decision = effectiveUnlisted === "auto" ? "human_required" : "auto";
    setUnlistedOverride(next === baseUnlisted ? null : next);
    setSubmitOk(false);
  }

  const dirty = Object.keys(overrides).length > 0 || unlistedOverride !== null;

  const save = useMutation({
    mutationFn: async () => {
      if (!selectedProjectId) throw new Error("Selecciona un proyecto.");
      const payload = {
        human_approval_policy: {
          // El slug viaja si el preset lo trae: es la segunda fuente de la que
          // el motor deriva `unlisted_category` si algún día falta la clave.
          ...(activePolicy?.categories.preset ? { preset: activePolicy.categories.preset } : {}),
          categories: effectiveDecisions,
          // Se escribe SIEMPRE, incluso valiendo lo mismo que el preset: una
          // política que no la lleva deja su comportamiento en manos de un
          // default del código, que es lo que el ADR 0153 vino a cerrar.
          unlisted_category: effectiveUnlisted,
        },
      };
      return apiFetch<Project>(`/projects/${selectedProjectId}`, {
        method: "PUT",
        body: payload,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects", "tenant"] });
      setSubmitError(null);
      setSubmitOk(true);
      setOverrides({});
      setUnlistedOverride(null);
    },
    onError: (err: unknown) => {
      setSubmitOk(false);
      setSubmitError(err instanceof ApiError ? err.body : String(err));
    },
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<ShieldCheck className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Validación humana"
        description="Elige una plantilla y, si lo necesitas, ajusta categorías concretas antes de aplicarla a un proyecto."
      />

      {policiesQuery.isLoading && (
        <p className="text-muted-foreground text-sm">Cargando plantillas…</p>
      )}

      {policiesQuery.isError && (
        <Card className="border-destructive p-4">
          <p className="text-destructive text-sm">
            Could not load policies:{" "}
            {policiesQuery.error instanceof ApiError
              ? policiesQuery.error.body
              : String(policiesQuery.error)}
          </p>
        </Card>
      )}

      {/* ============ Preset selector ============ */}
      {policies.length > 0 && (
        <section data-testid="presets-row" className="mb-6">
          <div
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
            data-testid="presets-grid"
          >
            {policies.map((p) => {
              const active = p.id === selectedPolicyId;
              const decisions = p.categories.categories;
              const humanCount = Object.values(decisions).filter(
                (d) => d === "human_required",
              ).length;
              return (
                <Card
                  key={p.id}
                  data-testid={`preset-${p.id}`}
                  data-active={active ? "true" : "false"}
                  interactive
                  onClick={() => setSelectedPolicyId(p.id)}
                  className={cn(
                    "flex h-full flex-col",
                    active && "border-primary shadow-md ring-1 ring-primary/30",
                  )}
                >
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">{p.name}</CardTitle>
                    <Badge
                      variant={
                        humanCount === 0 ? "success" : humanCount >= 10 ? "danger" : "warning"
                      }
                      className="w-fit"
                    >
                      {humanCount === 0
                        ? "Todo automático"
                        : `${humanCount}/${CATEGORY_LABELS.length} requieren humano`}
                    </Badge>
                  </CardHeader>
                  <CardContent>
                    <p className="text-muted-foreground text-xs">{p.description ?? ""}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      {/* ============ Category override table ============ */}
      {activePolicy && (
        <section data-testid="category-table" className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Categorías ({CATEGORY_LABELS.length})</CardTitle>
              <p className="text-muted-foreground text-xs">
                Plantilla base: <strong>{activePolicy.name}</strong>. Pulsa una celda para invertir
                la decisión de esa categoría — el override queda marcado y se aplica al guardar.
              </p>
            </CardHeader>
            <CardContent className="p-0">
              <ul className="divide-y" data-testid="category-list">
                {CATEGORY_LABELS.map(({ id, label, hint }) => {
                  const baseline = baseDecisions[id] ?? "auto";
                  const current = effectiveDecisions[id] ?? "auto";
                  const isOverride = baseline !== current;
                  const badge = DECISION_BADGE[current];
                  return (
                    <li
                      key={id}
                      data-testid={`category-${id}`}
                      data-decision={current}
                      data-override={isOverride ? "true" : "false"}
                      className="flex items-center justify-between gap-3 px-5 py-2.5"
                    >
                      <div>
                        <p className="text-sm font-medium">{label}</p>
                        <p className="text-muted-foreground text-xs">{hint}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        {isOverride && (
                          <Badge variant="info" data-testid={`override-${id}`}>
                            Override
                          </Badge>
                        )}
                        <button
                          type="button"
                          onClick={() => toggle(id)}
                          data-testid={`toggle-${id}`}
                          className={cn(
                            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                            current === "auto"
                              ? "bg-success-soft text-success-soft-foreground hover:bg-success-soft/80"
                              : "bg-warning-soft text-warning-soft-foreground hover:bg-warning-soft/80",
                          )}
                          aria-label={`Cambiar ${label} (actual: ${badge.label})`}
                        >
                          {badge.label}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
              {/* ADR 0153 — la 14.ª fila, que no es una categoría sino la regla
                  para las que no están en la lista. Va DENTRO de la misma tabla
                  a propósito: separada en otra tarjeta se lee como un ajuste
                  avanzado y nadie la mira, y es la que decide qué pasa con lo
                  que la política no nombra. */}
              <div
                data-testid="unlisted-category-row"
                data-decision={effectiveUnlisted}
                data-override={unlistedOverride !== null ? "true" : "false"}
                className="bg-muted/40 flex items-center justify-between gap-3 border-t px-5 py-3"
              >
                <div>
                  <p className="text-sm font-medium">Categoría no listada</p>
                  <p className="text-muted-foreground text-xs">
                    Qué hacer con una acción sensible cuya categoría no aparece arriba (una tool
                    nueva, o una política escrita a mano e incompleta). <strong>Humano</strong> es
                    la opción segura: para y pregunta.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {unlistedOverride !== null && (
                    <Badge variant="info" data-testid="override-unlisted-category">
                      Override
                    </Badge>
                  )}
                  <button
                    type="button"
                    onClick={toggleUnlisted}
                    data-testid="toggle-unlisted-category"
                    className={cn(
                      "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                      effectiveUnlisted === "auto"
                        ? "bg-success-soft text-success-soft-foreground hover:bg-success-soft/80"
                        : "bg-warning-soft text-warning-soft-foreground hover:bg-warning-soft/80",
                    )}
                    aria-label={`Cambiar categoria no listada (actual: ${DECISION_BADGE[effectiveUnlisted].label})`}
                  >
                    {DECISION_BADGE[effectiveUnlisted].label}
                  </button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ============ Apply to project ============ */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Aplicar a un proyecto</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="project">Proyecto</Label>
                <select
                  id="project"
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                  value={selectedProjectId}
                  onChange={(e) => {
                    setSelectedProjectId(e.target.value);
                    setSubmitOk(false);
                    setSubmitError(null);
                  }}
                  data-testid="project-select"
                  disabled={projects.length === 0}
                >
                  <option value="">— Selecciona —</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                {projects.length === 0 && (
                  <p className="text-muted-foreground text-xs" data-testid="no-projects-hint">
                    Este tenant aún no tiene proyectos. Crea uno desde /admin/projects/new para
                    poder guardar la política.
                  </p>
                )}
              </div>

              <div>
                <p className="text-muted-foreground text-xs uppercase">Resumen</p>
                <p className="text-sm">
                  {Object.values(effectiveDecisions).filter((d) => d === "auto").length} auto ·{" "}
                  {Object.values(effectiveDecisions).filter((d) => d === "human_required").length}{" "}
                  humano
                  {dirty && (
                    <Badge variant="info" className="ml-2" data-testid="dirty-badge">
                      Cambios sin guardar
                    </Badge>
                  )}
                </p>
              </div>

              {submitError && (
                <p
                  className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                  data-testid="submit-error"
                >
                  {submitError}
                </p>
              )}
              {submitOk && (
                <p
                  className="bg-success-soft text-success-soft-foreground rounded p-2 text-xs"
                  data-testid="submit-ok"
                >
                  Política aplicada al proyecto.
                </p>
              )}

              <Button
                onClick={() => save.mutate()}
                disabled={save.isPending || !selectedProjectId || projects.length === 0}
                data-testid="save-policy"
              >
                {save.isPending && <Spinner className="mr-2 h-4 w-4" />}
                {save.isPending ? "Guardando…" : "Aplicar política"}
              </Button>
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
