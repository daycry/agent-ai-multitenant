"use client";

/**
 * Alta y edición de una Knowledge Base (prod-16 `task_prod16_08`).
 *
 * Los dos diálogos comparten fichero porque comparten el formulario (nombre,
 * categoría, descripción) y el selector de categoría con su atajo «+ Nueva».
 * Lo que NO comparten está en el nombre: el alta manda el modelo de embedding,
 * la edición lo enseña como read-only y explica por qué.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import { CategoryCreateInlineDialog, CategorySelect } from "./kb-category-select";
import { DEFAULT_EMBEDDING_MODEL, type KbCategory, type KnowledgeBase } from "./kb-types";

interface KbForm {
  name: string;
  description: string | null;
  embedding_model_id: string;
  category_id: string | null;
}

export function KbCreateDialog({
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
  const t = useT("knowledgeBases");
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
            <DialogTitle>{t("createTitle")}</DialogTitle>
            <DialogDescription>{t("createDescription")}</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-name">{t("nameLabel")}</Label>
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
              <Label htmlFor="kb-create-category">{t("categoryLabel")}</Label>
              <CategorySelect
                value={categoryId}
                onChange={setCategoryId}
                categories={categories}
                onCreateRequested={() => setCreateCatOpen(true)}
                testId="kb-create-category"
              />
              <p className="text-muted-foreground text-xs">{t("categoryHelp")}</p>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{t("descriptionLabel")}</Label>
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
                {mutation.error?.message ?? t("createError")}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
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
              {mutation.isPending ? t("creating") : t("createSubmit")}
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

export function KbEditDialog({
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
  const t = useT("knowledgeBases");
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
            <DialogTitle>{t("editTitle")}</DialogTitle>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-edit-name">{t("nameLabel")}</Label>
              <Input
                id="kb-edit-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                data-testid="kb-edit-name"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kb-edit-category">{t("categoryLabel")}</Label>
              <CategorySelect
                value={categoryId}
                onChange={setCategoryId}
                categories={categories}
                onCreateRequested={() => setCreateCatOpen(true)}
                testId="kb-edit-category"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{t("descriptionLabel")}</Label>
              <MarkdownTextarea
                value={description}
                onChange={setDescription}
                rows={4}
                data-testid="kb-edit-description"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{t("embeddingLabel")}</Label>
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
              <p className="text-muted-foreground text-xs">{t("embeddingHelp")}</p>
            </div>

            {mutation.isError && (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="kb-edit-error"
              >
                {mutation.error?.message ?? t("saveError")}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
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
              {mutation.isPending ? t("saving") : t("save")}
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
