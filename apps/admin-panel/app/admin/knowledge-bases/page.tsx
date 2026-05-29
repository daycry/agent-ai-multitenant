"use client";

/**
 * Knowledge Bases — admin general del tenant.
 *
 * Plan 06.10: agrupa el listado por categoría (built-in + custom) y
 * añade selector de categoría en Crear/Editar. La gestión de
 * categorías (crear, editar, borrar) vive en
 * `/admin/knowledge-bases/categories`. Desde Crear/Editar hay un atajo
 * "+ Nueva" que abre un mini-dialog inline sin salir del flujo.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Home, Library, Pencil, Plus, Share2, Tag, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";

import { KbAssignmentsDialog } from "./kb-assignments-dialog";
import { KbDocumentsPanel } from "./kb-documents-panel";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { ApiError, apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";

interface KbCategorySummary {
  id: string;
  slug: string;
  name: string;
  color: string | null;
  is_builtin: boolean;
}

interface KbCategory extends KbCategorySummary {
  tenant_id: string | null;
}

interface KnowledgeBase {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  embedding_model_id: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  is_builtin: boolean;
  category: KbCategorySummary | null;
}

const DEFAULT_EMBEDDING_MODEL = "nomic-embed-text-v1.5";
const NO_CATEGORY_KEY = "__none__";

export default function KnowledgeBasesPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeBase | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const [granting, setGranting] = useState<KnowledgeBase | null>(null);
  const [assignmentsFor, setAssignmentsFor] = useState<KnowledgeBase | null>(null);

  const kbsQuery = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => apiFetch<KnowledgeBase[]>("/knowledge-bases"),
    refetchOnWindowFocus: false,
  });

  const categoriesQuery = useQuery({
    queryKey: ["kb-categories"],
    queryFn: () => apiFetch<KbCategory[]>("/kb-categories"),
    refetchOnWindowFocus: false,
  });

  const kbs = kbsQuery.data ?? [];
  const categories = categoriesQuery.data ?? [];

  function refetch() {
    void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
  }
  function refetchCategories() {
    void queryClient.invalidateQueries({ queryKey: ["kb-categories"] });
  }

  const grouped = useMemo(() => groupByCategory(kbs, categories), [kbs, categories]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="kbs-page">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Knowledge Bases" },
        ]}
      />
      <PageHeader
        icon={<Library className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Knowledge Bases"
        description="Bases de conocimiento del tenant. Cada KB agrupa documentos indexados y se asigna (grant) a uno o más proyectos."
        actions={
          <RoleGuard min="tenant_admin">
            <div className="flex flex-row items-center gap-2">
              <Button asChild variant="outline" data-testid="kbs-categories-link">
                <Link href="/admin/knowledge-bases/categories">
                  <Tag className="mr-1 h-4 w-4" />
                  Categorías
                </Link>
              </Button>
              <Button onClick={() => setCreateOpen(true)} data-testid="kbs-create-button">
                <Plus className="mr-1 h-4 w-4" />
                Crear KB
              </Button>
            </div>
          </RoleGuard>
        }
      />

      <div className="mt-6">
        {kbsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Cargando KBs…</p>
        ) : kbsQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>Error</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-destructive text-sm" data-testid="kbs-error">
                {kbsQuery.error instanceof ApiError ? kbsQuery.error.body : String(kbsQuery.error)}
              </p>
            </CardContent>
          </Card>
        ) : kbs.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm" data-testid="kbs-empty">
                Aún no hay KBs en este tenant. Crea la primera para empezar a indexar documentos.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6" data-testid="kbs-list">
            {grouped.map((group) => (
              <section key={group.key} data-testid={`kb-group-${group.key}`}>
                <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.category && (
                    <span
                      className="inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: group.category.color ?? "#94a3b8" }}
                    />
                  )}
                  {group.label}
                  <span className="text-muted-foreground/70 text-xs font-normal">
                    ({group.kbs.length})
                  </span>
                </h2>
                <ul className="space-y-3">
                  {group.kbs.map((kb) => (
                    <li key={kb.id}>
                      <KbRow
                        kb={kb}
                        onEdit={() => setEditing(kb)}
                        onDelete={() => setDeleting(kb)}
                        onGrant={() => setGranting(kb)}
                        onShowAssignments={() => setAssignmentsFor(kb)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>

      {assignmentsFor && (
        <KbAssignmentsDialog
          kbId={assignmentsFor.id}
          kbName={assignmentsFor.name}
          open={true}
          onOpenChange={(v) => !v && setAssignmentsFor(null)}
        />
      )}

      <KbCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        categories={categories}
        onCategoriesChanged={refetchCategories}
        onCreated={() => {
          refetch();
          setCreateOpen(false);
        }}
      />

      {editing && (
        <KbEditDialog
          kb={editing}
          categories={categories}
          onCategoriesChanged={refetchCategories}
          onOpenChange={(v) => !v && setEditing(null)}
          onSaved={() => {
            refetch();
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <KbDeleteDialog
          kb={deleting}
          onOpenChange={(v) => !v && setDeleting(null)}
          onDeleted={() => {
            refetch();
            setDeleting(null);
          }}
        />
      )}

      {granting && (
        <KbGrantDialog
          kb={granting}
          onOpenChange={(v) => !v && setGranting(null)}
          onGranted={() => setGranting(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouping helper
// ---------------------------------------------------------------------------

interface KbGroup {
  key: string;
  label: string;
  category: KbCategorySummary | null;
  kbs: KnowledgeBase[];
}

function groupByCategory(kbs: KnowledgeBase[], categories: KbCategory[]): KbGroup[] {
  const byId = new Map<string, KbGroup>();
  const sinCat: KbGroup = {
    key: NO_CATEGORY_KEY,
    label: "Sin categoría",
    category: null,
    kbs: [],
  };
  // Pre-cargar grupos para todas las categorías visibles, así aparecen
  // como secciones vacías si el usuario las creó pero aún no asignó
  // ninguna KB (UX más predecible).
  for (const c of categories) {
    byId.set(c.id, { key: c.id, label: c.name, category: c, kbs: [] });
  }
  for (const kb of kbs) {
    if (kb.category) {
      const g = byId.get(kb.category.id);
      if (g) g.kbs.push(kb);
      else
        // Categoría borrada en otro tab; mostramos KB en "Sin categoría".
        sinCat.kbs.push(kb);
    } else {
      sinCat.kbs.push(kb);
    }
  }
  // Orden: built-ins primero (por nombre), después custom (por nombre),
  // "Sin categoría" al final.
  const out: KbGroup[] = [];
  const groups = Array.from(byId.values()).filter((g) => g.kbs.length > 0);
  groups.sort((a, b) => {
    const aBuiltin = a.category?.is_builtin ? 0 : 1;
    const bBuiltin = b.category?.is_builtin ? 0 : 1;
    if (aBuiltin !== bBuiltin) return aBuiltin - bBuiltin;
    return a.label.localeCompare(b.label);
  });
  out.push(...groups);
  if (sinCat.kbs.length > 0) out.push(sinCat);
  return out;
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function KbRow({
  kb,
  onEdit,
  onDelete,
  onGrant,
  onShowAssignments,
}: {
  kb: KnowledgeBase;
  onEdit: () => void;
  onDelete: () => void;
  onGrant: () => void;
  onShowAssignments: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card data-testid={`kb-${kb.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
          data-testid={`kb-toggle-docs-${kb.id}`}
          aria-expanded={expanded}
        >
          <ChevronRight
            className={`mt-1 h-4 w-4 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
          />
          <span className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-base">
              {kb.name}
              {kb.is_builtin && (
                <Badge variant="muted" data-testid={`kb-builtin-badge-${kb.id}`}>
                  Built-in
                </Badge>
              )}
            </CardTitle>
            <span className="text-muted-foreground mt-1 block font-mono text-xs">
              embedding: {kb.embedding_model_id}
            </span>
          </span>
        </button>
        <Button
          variant="outline"
          size="sm"
          onClick={onShowAssignments}
          data-testid={`kb-assignments-${kb.id}`}
          title="Ver proyectos y agentes con grant"
        >
          Asignaciones
        </Button>
        <RoleGuard min="tenant_admin">
          <div className="flex flex-row items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={onGrant}
              data-testid={`kb-grant-${kb.id}`}
              title="Dar acceso a un proyecto"
            >
              <Share2 className="mr-1 h-3.5 w-3.5" />
              Grant
            </Button>
            {/* Plan 06.12: las KB built-in son read-only para el tenant
                (el backend rechaza PUT/DELETE). Solo Grant + Asignaciones. */}
            {!kb.is_builtin && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onEdit}
                  data-testid={`kb-edit-${kb.id}`}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onDelete}
                  data-testid={`kb-delete-${kb.id}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </>
            )}
          </div>
        </RoleGuard>
      </CardHeader>
      {kb.description && (
        <CardContent className="pb-2">
          <div className="text-sm">{renderPlanDraft(kb.description)}</div>
        </CardContent>
      )}
      {expanded && (
        <CardContent className="pt-0">
          <KbDocumentsPanel kbId={kb.id} />
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Category Selector (compartido por Create + Edit)
// ---------------------------------------------------------------------------

function CategorySelect({
  value,
  onChange,
  categories,
  onCreateRequested,
  testId,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  categories: KbCategory[];
  onCreateRequested: () => void;
  testId?: string;
}) {
  return (
    <div className="flex flex-row items-center gap-2">
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        className="border-input bg-background h-9 flex-1 rounded-md border px-3 text-sm"
        data-testid={testId}
      >
        <option value="">— Sin categoría —</option>
        <optgroup label="Built-in">
          {categories
            .filter((c) => c.is_builtin)
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </optgroup>
        {categories.some((c) => !c.is_builtin) && (
          <optgroup label="Tenant">
            {categories
              .filter((c) => !c.is_builtin)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </optgroup>
        )}
      </select>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onCreateRequested}
        title="Crear categoría nueva"
        data-testid={`${testId}-create`}
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

interface KbForm {
  name: string;
  description: string | null;
  embedding_model_id: string;
  category_id: string | null;
}

function KbCreateDialog({
  open,
  onOpenChange,
  categories,
  onCategoriesChanged,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  categories: KbCategory[];
  onCategoriesChanged: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [createCatOpen, setCreateCatOpen] = useState(false);

  const mutation = useMutation<KnowledgeBase, ApiError, KbForm>({
    mutationFn: (payload) =>
      apiFetch<KnowledgeBase>("/knowledge-bases", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      setName("");
      setDescription("");
      setCategoryId(null);
      onCreated();
    },
  });

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!v) {
            setName("");
            setDescription("");
            setCategoryId(null);
          }
          onOpenChange(v);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Crear Knowledge Base</DialogTitle>
            <DialogDescription>
              Una KB es un contenedor de documentos indexados. Tras crearla, despliégala en esta
              misma lista para subir documentos, y dale acceso (grant) a los proyectos o agentes que
              la consumirán.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-name">Nombre</Label>
              <Input
                id="kb-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                data-testid="kb-create-name"
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-create-category">Categoría</Label>
              <CategorySelect
                value={categoryId}
                onChange={setCategoryId}
                categories={categories}
                onCreateRequested={() => setCreateCatOpen(true)}
                testId="kb-create-category"
              />
              <p className="text-muted-foreground text-xs">
                Las categorías ayudan a organizar el listado. Opcional.
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Descripción</Label>
              <MarkdownTextarea
                value={description}
                onChange={setDescription}
                rows={4}
                data-testid="kb-create-description"
              />
            </div>

            {mutation.isError && (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="kb-create-error"
              >
                {mutation.error?.message ?? "Error al crear"}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button
              disabled={!name.trim() || mutation.isPending}
              onClick={() =>
                mutation.mutate({
                  name: name.trim(),
                  description: description.trim() || null,
                  embedding_model_id: DEFAULT_EMBEDDING_MODEL,
                  category_id: categoryId,
                })
              }
              data-testid="kb-create-submit"
            >
              {mutation.isPending ? "Creando…" : "Crear KB"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryCreateInlineDialog
        open={createCatOpen}
        onOpenChange={setCreateCatOpen}
        onCreated={(id) => {
          setCategoryId(id);
          setCreateCatOpen(false);
          onCategoriesChanged();
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------

function KbEditDialog({
  kb,
  categories,
  onCategoriesChanged,
  onOpenChange,
  onSaved,
}: {
  kb: KnowledgeBase;
  categories: KbCategory[];
  onCategoriesChanged: () => void;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(kb.name);
  const [description, setDescription] = useState(kb.description ?? "");
  const [categoryId, setCategoryId] = useState<string | null>(kb.category?.id ?? null);
  const [createCatOpen, setCreateCatOpen] = useState(false);

  const mutation = useMutation<KnowledgeBase, ApiError, Partial<KbForm>>({
    mutationFn: (payload) =>
      apiFetch<KnowledgeBase>(`/knowledge-bases/${kb.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <>
      <Dialog open={true} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Editar Knowledge Base</DialogTitle>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-edit-name">Nombre</Label>
              <Input
                id="kb-edit-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                data-testid="kb-edit-name"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-edit-category">Categoría</Label>
              <CategorySelect
                value={categoryId}
                onChange={setCategoryId}
                categories={categories}
                onCreateRequested={() => setCreateCatOpen(true)}
                testId="kb-edit-category"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Descripción</Label>
              <MarkdownTextarea
                value={description}
                onChange={setDescription}
                rows={4}
                data-testid="kb-edit-description"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Modelo de embedding</Label>
              {/* Read-only por diseño: cambiar el modelo invalida los
                  embeddings de los chunks existentes (las queries no
                  matchean) y rompe el RAG. El re-embedding pipeline
                  llega con Plan 12; hasta entonces el operador del
                  stack lo configura por seed, no por UI. */}
              <p
                className="bg-muted/40 text-muted-foreground rounded border px-3 py-2 font-mono text-xs"
                data-testid="kb-edit-embedding"
              >
                {kb.embedding_model_id}
              </p>
              <p className="text-muted-foreground text-xs">
                El modelo es fijo por KB. Para usar otro, crea una KB nueva y reindexa los
                documentos.
              </p>
            </div>

            {mutation.isError && (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="kb-edit-error"
              >
                {mutation.error?.message ?? "Error al guardar"}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button
              disabled={!name.trim() || mutation.isPending}
              onClick={() =>
                mutation.mutate({
                  name: name.trim(),
                  description: description.trim() || null,
                  category_id: categoryId,
                })
              }
              data-testid="kb-edit-submit"
            >
              {mutation.isPending ? "Guardando…" : "Guardar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryCreateInlineDialog
        open={createCatOpen}
        onOpenChange={setCreateCatOpen}
        onCreated={(id) => {
          setCategoryId(id);
          setCreateCatOpen(false);
          onCategoriesChanged();
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Category — mini dialog inline (POST /kb-categories)
// ---------------------------------------------------------------------------

function CategoryCreateInlineDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [color, setColor] = useState("#64748b");

  const mutation = useMutation<KbCategory, ApiError, { slug: string; name: string; color: string }>(
    {
      mutationFn: (payload) =>
        apiFetch<KbCategory>("/kb-categories", { method: "POST", body: payload }),
      onSuccess: (cat) => {
        setSlug("");
        setName("");
        setColor("#64748b");
        onCreated(cat.id);
      },
    },
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setSlug("");
          setName("");
          setColor("#64748b");
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nueva categoría</DialogTitle>
          <DialogDescription>
            Crea una categoría para organizar tus KBs. El slug es el identificador estable que se
            usa en filtros y URLs.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-slug">Slug</Label>
            <Input
              id="cat-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase())}
              placeholder="ej. compliance-pci"
              pattern="[a-z0-9][a-z0-9_-]*"
              data-testid="cat-inline-slug"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-name">Nombre</Label>
            <Input
              id="cat-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ej. Compliance PCI-DSS"
              data-testid="cat-inline-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-color">Color</Label>
            <div className="flex flex-row items-center gap-2">
              <input
                id="cat-color"
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="h-9 w-12 cursor-pointer rounded border"
              />
              <Input
                value={color}
                onChange={(e) => setColor(e.target.value)}
                placeholder="#64748b"
                className="font-mono"
              />
            </div>
          </div>
          {mutation.isError && (
            <p className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs">
              {mutation.error?.message ?? "Error al crear categoría"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!slug.trim() || !name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                slug: slug.trim(),
                name: name.trim(),
                color,
              })
            }
            data-testid="cat-inline-submit"
          >
            {mutation.isPending ? "Creando…" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete with confirm-by-name
// ---------------------------------------------------------------------------

function KbDeleteDialog({
  kb,
  onOpenChange,
  onDeleted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === kb.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/knowledge-bases/${kb.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) setTyped("");
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar Knowledge Base</DialogTitle>
          <DialogDescription>
            Borra la KB, todos sus documentos indexados y los grants a proyectos. La acción es{" "}
            <strong>irreversible</strong>. Los documentos en MinIO no se tocan.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Para confirmar, teclea el nombre de la KB:{" "}
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{kb.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={kb.name}
            data-testid="kb-delete-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-delete-error"
            >
              {mutation.error?.message ?? "Error al borrar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="kb-delete-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Grant to project
// ---------------------------------------------------------------------------

function KbGrantDialog({
  kb,
  onOpenChange,
  onGranted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onGranted: () => void;
}) {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const mutation = useMutation<unknown, ApiError, { project_id: string }>({
    mutationFn: (payload) =>
      apiFetch(`/knowledge-bases/${kb.id}/projects`, { method: "POST", body: payload }),
    onSuccess: () => {
      setSuccessMsg(
        projectName ? `Acceso otorgado a "${projectName}".` : "Acceso otorgado al proyecto.",
      );
      setProjectId(null);
      setProjectName(null);
    },
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) {
          setProjectId(null);
          setProjectName(null);
          setSuccessMsg(null);
          onGranted();
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dar acceso a un proyecto</DialogTitle>
          <DialogDescription>
            Después del grant, el proyecto verá esta KB en su sub-sección &quot;Knowledge
            Bases&quot; y podrá subir documentos. Puedes hacer grant a varios proyectos repitiendo
            esta acción.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            KB: <strong>{kb.name}</strong>
          </p>

          <div className="flex flex-col gap-1.5">
            <Label>Proyecto destino</Label>
            <ProjectCombobox
              value={projectId}
              onChange={(id, name) => {
                setProjectId(id);
                setProjectName(name ?? null);
                setSuccessMsg(null);
              }}
              data-testid="kb-grant-project"
            />
          </div>

          {successMsg && (
            <p
              className="bg-success-soft text-success-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-success"
            >
              {successMsg}
            </p>
          )}

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-error"
            >
              {mutation.error?.message ?? "Error al otorgar acceso"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cerrar
          </Button>
          <Button
            disabled={!projectId || mutation.isPending}
            onClick={() => {
              if (projectId) mutation.mutate({ project_id: projectId });
            }}
            data-testid="kb-grant-submit"
          >
            {mutation.isPending ? "Otorgando…" : "Otorgar acceso"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
