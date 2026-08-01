"use client";

/**
 * Selector de categoría + el mini-diálogo «+ Nueva» que lo acompaña
 * (prod-16 `task_prod16_08`).
 *
 * Las dos piezas viajan juntas porque el atajo «+» sólo existe para que dar de
 * alta una KB no obligue a salir del flujo a la pantalla de categorías: separar
 * el `<select>` de su diálogo dejaría dos ficheros que nadie usa por separado.
 * Lo comparten el alta y la edición de KB.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Plus } from "lucide-react";

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
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import { type KbCategory } from "./kb-types";

const DEFAULT_COLOR = "#64748b";

export function CategorySelect({
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
  const t = useT("knowledgeBases");

  return (
    <div className="flex flex-row items-center gap-2">
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        className="border-input bg-background h-9 flex-1 rounded-md border px-3 text-sm"
        data-testid={testId}
      >
        <option value="">{t("noCategoryOption")}</option>
        <optgroup label={t("categoryGroupBuiltin")}>
          {categories
            .filter((c) => c.is_builtin)
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </optgroup>
        {categories.some((c) => !c.is_builtin) && (
          <optgroup label={t("categoryGroupTenant")}>
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
        title={t("newCategoryTitle")}
        data-testid={`${testId}-create`}
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

export function CategoryCreateInlineDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const t = useT("knowledgeBases");
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEFAULT_COLOR);

  const mutation = useMutation<KbCategory, ApiError, { slug: string; name: string; color: string }>(
    {
      mutationFn: (payload) =>
        apiFetch<KbCategory>("/kb-categories", { method: "POST", body: payload }),
      onSuccess: (cat) => {
        setSlug("");
        setName("");
        setColor(DEFAULT_COLOR);
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
          setColor(DEFAULT_COLOR);
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("inlineCatTitle")}</DialogTitle>
          <DialogDescription>{t("inlineCatDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-slug">{t("slugLabel")}</Label>
            <Input
              id="cat-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase())}
              placeholder={t("slugPlaceholder")}
              pattern="[a-z0-9][a-z0-9_-]*"
              data-testid="cat-inline-slug"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-name">{t("nameLabel")}</Label>
            <Input
              id="cat-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("catNamePlaceholder")}
              data-testid="cat-inline-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cat-color">{t("colorLabel")}</Label>
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
              />
            </div>
          </div>
          {mutation.isError && (
            <p className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs">
              {mutation.error?.message ?? t("inlineCatError")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
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
            {mutation.isPending ? t("creating") : t("inlineCatSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
