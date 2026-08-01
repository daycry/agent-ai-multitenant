"use client";

/**
 * Knowledge Bases — admin general del tenant.
 *
 * Plan 06.10: agrupa el listado por categoría (built-in + custom) y
 * añade selector de categoría en Crear/Editar. La gestión de
 * categorías (crear, editar, borrar) vive en
 * `/admin/knowledge-bases/categories`. Desde Crear/Editar hay un atajo
 * "+ Nueva" que abre un mini-dialog inline sin salir del flujo.
 *
 * prod-16 `task_prod16_04` + `task_prod16_08`: la pantalla está migrada al
 * diccionario (`useT("knowledgeBases")`) y sus piezas viven en secciones
 * colocadas — `kb-row`, `kb-form-dialogs`, `kb-danger-dialogs`,
 * `kb-category-select` — en vez de en un `kb-sections.tsx` de 782 líneas.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Home, Library, Plus, Tag } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { KbAssignmentsDialog } from "./kb-assignments-dialog";
import { KbDeleteDialog, KbGrantDialog } from "./kb-danger-dialogs";
import { KbCreateDialog, KbEditDialog } from "./kb-form-dialogs";
import { KbRow } from "./kb-row";
import { groupByCategory, type KbCategory, type KnowledgeBase } from "./kb-types";

export default function KnowledgeBasesPage() {
  const t = useT("knowledgeBases");
  const errorText = useErrorText();
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

  const uncategorized = t("uncategorized");
  const grouped = useMemo(
    () => groupByCategory(kbs, categories, uncategorized),
    [kbs, categories, uncategorized],
  );

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="kbs-page">
      <Breadcrumb
        items={[
          { label: t("home"), href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: t("title") },
        ]}
      />
      <PageHeader
        icon={<Library className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        actions={
          <RoleGuard min="tenant_admin">
            <div className="flex flex-row items-center gap-2">
              <Button asChild variant="outline" data-testid="kbs-categories-link">
                <Link href="/admin/knowledge-bases/categories">
                  <Tag className="mr-1 h-4 w-4" />
                  {t("categoriesLink")}
                </Link>
              </Button>
              <Button onClick={() => setCreateOpen(true)} data-testid="kbs-create-button">
                <Plus className="mr-1 h-4 w-4" />
                {t("createButton")}
              </Button>
            </div>
          </RoleGuard>
        }
      />

      <div className="mt-6">
        {kbsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">{t("loading")}</p>
        ) : kbsQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("errorTitle")}</CardTitle>
            </CardHeader>
            <CardContent>
              {/* prod-16 `task_prod16_05`: `errorText` humaniza el `detail` del
                  backend. Antes esto pintaba `error.body` CRUDO, así que un 502
                  de nginx o un traceback acababan en pantalla. */}
              <p className="text-destructive text-sm" data-testid="kbs-error">
                {errorText(kbsQuery.error)}
              </p>
            </CardContent>
          </Card>
        ) : kbs.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm" data-testid="kbs-empty">
                {t("empty")}
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6" data-testid="kbs-list">
            {grouped.map((group) => (
              <section key={group.key} data-testid={`kb-group-${group.key}`}>
                <h2 className="text-muted-foreground mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide">
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
