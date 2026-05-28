"use client";

/**
 * Categorías de Knowledge Bases — admin (Plan 06.10 task_06_10_09).
 *
 * Las categorías agrupan KBs en la UI principal. La plataforma siembra
 * 5 built-in (stack / role / compliance / architecture / process) y el
 * tenant puede crear las suyas custom desde aquí.
 *
 * Built-ins son read-only: el endpoint backend rechaza PUT/DELETE con
 * 403 sobre `tenant_id IS NULL`, y la UI lo refleja con badges + sin
 * botones de editar / borrar.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Home, Library, Pencil, Plus, Tag, Trash2 } from "lucide-react";

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
import { ApiError, apiFetch } from "@/lib/api";

interface KbCategory {
  id: string;
  tenant_id: string | null;
  slug: string;
  name: string;
  color: string | null;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

const DEFAULT_COLOR = "#64748b";

export default function KbCategoriesPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<KbCategory | null>(null);
  const [deleting, setDeleting] = useState<KbCategory | null>(null);

  const catsQuery = useQuery({
    queryKey: ["kb-categories"],
    queryFn: () => apiFetch<KbCategory[]>("/kb-categories"),
    refetchOnWindowFocus: false,
  });

  const cats = catsQuery.data ?? [];
  const builtins = cats.filter((c) => c.is_builtin);
  const custom = cats.filter((c) => !c.is_builtin);

  function refetch() {
    void queryClient.invalidateQueries({ queryKey: ["kb-categories"] });
  }

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="kb-categories-page"
    >
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          {
            label: "Knowledge Bases",
            href: "/admin/knowledge-bases",
            icon: <Library className="h-3.5 w-3.5" />,
          },
          { label: "Categorías" },
        ]}
      />
      <PageHeader
        icon={<Tag className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Categorías de KBs"
        description="Organiza tus knowledge bases en grupos. Las built-in vienen sembradas por la plataforma y son comunes a todos los tenants."
        actions={
          <RoleGuard min="tenant_admin">
            <Button onClick={() => setCreateOpen(true)} data-testid="kb-cat-create-button">
              <Plus className="mr-1 h-4 w-4" />
              Nueva categoría
            </Button>
          </RoleGuard>
        }
      />

      {catsQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">Cargando categorías…</p>
      ) : catsQuery.isError ? (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-destructive text-sm">
              {catsQuery.error instanceof ApiError ? catsQuery.error.body : String(catsQuery.error)}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="mt-6 space-y-6">
          <section>
            <h2 className="text-muted-foreground mb-2 text-sm font-semibold uppercase tracking-wide">
              Built-in ({builtins.length})
            </h2>
            <ul className="space-y-2">
              {builtins.map((c) => (
                <li key={c.id}>
                  <CategoryRow category={c} readonly />
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-muted-foreground mb-2 text-sm font-semibold uppercase tracking-wide">
              Tenant ({custom.length})
            </h2>
            {custom.length === 0 ? (
              <Card>
                <CardContent className="py-8 text-center">
                  <p className="text-muted-foreground text-sm">
                    No has creado categorías propias todavía. Usa las built-in o crea una nueva.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <ul className="space-y-2" data-testid="kb-cat-custom-list">
                {custom.map((c) => (
                  <li key={c.id}>
                    <CategoryRow
                      category={c}
                      onEdit={() => setEditing(c)}
                      onDelete={() => setDeleting(c)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}

      <CategoryCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => {
          refetch();
          setCreateOpen(false);
        }}
      />

      {editing && (
        <CategoryEditDialog
          category={editing}
          onOpenChange={(v) => !v && setEditing(null)}
          onSaved={() => {
            refetch();
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <CategoryDeleteDialog
          category={deleting}
          onOpenChange={(v) => !v && setDeleting(null)}
          onDeleted={() => {
            refetch();
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function CategoryRow({
  category,
  readonly,
  onEdit,
  onDelete,
}: {
  category: KbCategory;
  readonly?: boolean;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  return (
    <Card data-testid={`kb-cat-${category.slug}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 py-3">
        <div className="flex flex-1 flex-row items-center gap-3">
          <span
            className="inline-block h-4 w-4 rounded-full border"
            style={{ backgroundColor: category.color ?? "#cbd5e1" }}
          />
          <div className="flex flex-col">
            <CardTitle className="text-base">{category.name}</CardTitle>
            <p className="text-muted-foreground font-mono text-xs">{category.slug}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {category.is_builtin && <Badge variant="muted">Built-in</Badge>}
          {!readonly && (
            <RoleGuard min="tenant_admin">
              <Button
                variant="outline"
                size="sm"
                onClick={onEdit}
                data-testid={`kb-cat-edit-${category.slug}`}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={onDelete}
                data-testid={`kb-cat-delete-${category.slug}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </RoleGuard>
          )}
        </div>
      </CardHeader>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

interface CategoryForm {
  slug: string;
  name: string;
  color: string;
}

function CategoryCreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEFAULT_COLOR);

  const mutation = useMutation<KbCategory, ApiError, CategoryForm>({
    mutationFn: (payload) =>
      apiFetch<KbCategory>("/kb-categories", { method: "POST", body: payload }),
    onSuccess: () => {
      setSlug("");
      setName("");
      setColor(DEFAULT_COLOR);
      onCreated();
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setSlug("");
          setName("");
          setColor(DEFAULT_COLOR);
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nueva categoría</DialogTitle>
          <DialogDescription>
            El slug es el identificador estable que se usa en filtros y URLs (sólo a-z, 0-9, `_`,
            `-`). El nombre es el texto que se muestra en la UI.
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
              data-testid="kb-cat-slug"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-name">Nombre</Label>
            <Input
              id="cat-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ej. Compliance PCI-DSS"
              data-testid="kb-cat-name"
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
                placeholder={DEFAULT_COLOR}
                className="font-mono"
                data-testid="kb-cat-color"
              />
            </div>
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-cat-error"
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
            disabled={!slug.trim() || !name.trim() || mutation.isPending}
            onClick={() => mutation.mutate({ slug: slug.trim(), name: name.trim(), color })}
            data-testid="kb-cat-submit"
          >
            {mutation.isPending ? "Creando…" : "Crear categoría"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------

function CategoryEditDialog({
  category,
  onOpenChange,
  onSaved,
}: {
  category: KbCategory;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(category.name);
  const [color, setColor] = useState(category.color ?? DEFAULT_COLOR);

  const mutation = useMutation<KbCategory, ApiError, { name?: string; color?: string }>({
    mutationFn: (payload) =>
      apiFetch<KbCategory>(`/kb-categories/${category.id}`, { method: "PUT", body: payload }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar categoría</DialogTitle>
          <DialogDescription>
            El slug no se puede cambiar — está sembrado en filtros y posibles integraciones.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label>Slug</Label>
            <p className="bg-muted/40 text-muted-foreground rounded border px-3 py-2 font-mono text-xs">
              {category.slug}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-edit-name">Nombre</Label>
            <Input
              id="cat-edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="kb-cat-edit-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-edit-color">Color</Label>
            <div className="flex flex-row items-center gap-2">
              <input
                id="cat-edit-color"
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="h-9 w-12 cursor-pointer rounded border"
              />
              <Input
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="font-mono"
              />
            </div>
          </div>
          {mutation.isError && (
            <p className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs">
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
            onClick={() => mutation.mutate({ name: name.trim(), color })}
            data-testid="kb-cat-edit-submit"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

function CategoryDeleteDialog({
  category,
  onOpenChange,
  onDeleted,
}: {
  category: KbCategory;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/kb-categories/${category.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar categoría</DialogTitle>
          <DialogDescription>
            Las KBs que pertenecían a esta categoría quedarán sin categoría (no se borran).
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            ¿Borrar la categoría <strong>{category.name}</strong> (
            <code className="text-xs">{category.slug}</code>)?
          </p>
          {mutation.isError && (
            <p className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs">
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
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="kb-cat-delete-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
