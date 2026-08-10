"use client";

/**
 * Los dos diálogos del catálogo de tools: alta/edición de una tool custom y
 * confirmación de borrado. Las built-in son de solo lectura y no llegan aquí.
 *
 * Pieza colocada, sacada de `page.tsx` al trocearlo (prod-16 `task_prod16_08`).
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

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
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";
import {
  CATEGORY,
  IMPL,
  SECURITY,
  type ImplementationType,
  type SecurityLevel,
  type ToolCategory,
} from "@/lib/tools/taxonomy";
import { useErrorText } from "@/lib/use-error-text";

import { facetLabel } from "./tool-facet-select";
import { EMPTY_FORM, type CatalogTool, type ToolFormValue } from "./tool-types";

// ---------------------------------------------------------------------------
// Create / edit dialog — custom tools only (built-in are read-only)
// ---------------------------------------------------------------------------
export function ToolFormDialog({
  open,
  onOpenChange,
  editing,
  lang,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  editing: CatalogTool | null;
  lang: Lang;
}) {
  const queryClient = useQueryClient();
  const t = useT("tools");
  const errorText = useErrorText();
  const isEdit = editing !== null;

  const [form, setForm] = useState<ToolFormValue>(() =>
    editing
      ? {
          name: editing.name,
          description: editing.description ?? "",
          category: (editing.category as ToolCategory) ?? "custom",
          implementation_type:
            (editing.implementation_type as ImplementationType) ?? "http_endpoint",
          implementation_ref: editing.implementation_ref ?? "",
          security_level: (editing.security_level as SecurityLevel) ?? "safe",
        }
      : EMPTY_FORM,
  );

  const mutation = useMutation<CatalogTool, ApiError, void>({
    mutationFn: () => {
      const body = {
        name: form.name,
        description: form.description.trim() === "" ? null : form.description,
        category: form.category,
        implementation_type: form.implementation_type,
        implementation_ref: form.implementation_ref.trim() === "" ? null : form.implementation_ref,
        security_level: form.security_level,
      };
      if (isEdit && editing) {
        return apiFetch<CatalogTool>(`/tools/${editing.id}`, { method: "PUT", body });
      }
      return apiFetch<CatalogTool>("/tools", { method: "POST", body });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools-catalog"] });
      onOpenChange(false);
    },
  });

  const set = <K extends keyof ToolFormValue>(key: K, value: ToolFormValue[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // 409 from the backend = duplicate / collides with a built-in (task_06_18_04).
  // Para el resto, `errorText` (prod-16 `task_prod16_05`): antes esto pintaba
  // `mutation.error.body` CRUDO, o sea el cuerpo del backend en pantalla.
  const errorMessage = !mutation.isError
    ? null
    : mutation.error?.status === 409
      ? t("duplicateError")
      : errorText(mutation.error);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg">
      <DialogContent data-testid="tool-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("dialogEditTitle") : t("dialogCreateTitle")}</DialogTitle>
          <DialogDescription>{t("dialogDescription")}</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <DialogBody>
            <div className="space-y-1.5">
              <Label htmlFor="tool-form-name">{t("fieldName")}</Label>
              <Input
                id="tool-form-name"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder={t("fieldNamePlaceholder")}
                required
                data-testid="tool-form-name"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="tool-form-description">{t("fieldDescription")}</Label>
              <Input
                id="tool-form-description"
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder={t("fieldDescriptionPlaceholder")}
                data-testid="tool-form-description"
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-category">{t("facetCategory")}</Label>
                <Select
                  id="tool-form-category"
                  value={form.category}
                  onChange={(e) => set("category", e.target.value as ToolCategory)}
                  data-testid="tool-form-category"
                >
                  {Object.keys(CATEGORY).map((slug) => (
                    <option key={slug} value={slug}>
                      {facetLabel(CATEGORY[slug as ToolCategory], slug, lang)}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-impl">{t("facetImpl")}</Label>
                <Select
                  id="tool-form-impl"
                  value={form.implementation_type}
                  onChange={(e) => set("implementation_type", e.target.value as ImplementationType)}
                  data-testid="tool-form-impl"
                >
                  {Object.keys(IMPL)
                    // `builtin` is platform-only; a tenant tool is never built-in.
                    .filter((slug) => slug !== "builtin")
                    .map((slug) => (
                      <option key={slug} value={slug}>
                        {facetLabel(IMPL[slug as ImplementationType], slug, lang)}
                      </option>
                    ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-security">{t("facetSecurity")}</Label>
                <Select
                  id="tool-form-security"
                  value={form.security_level}
                  onChange={(e) => set("security_level", e.target.value as SecurityLevel)}
                  data-testid="tool-form-security"
                >
                  {Object.keys(SECURITY).map((slug) => (
                    <option key={slug} value={slug}>
                      {facetLabel(SECURITY[slug as SecurityLevel], slug, lang)}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="tool-form-ref">{t("fieldRef")}</Label>
              <Input
                id="tool-form-ref"
                value={form.implementation_ref}
                onChange={(e) => set("implementation_ref", e.target.value)}
                placeholder={t("fieldRefPlaceholder")}
                data-testid="tool-form-ref"
              />
            </div>

            {errorMessage && (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="tool-form-error"
              >
                {errorMessage}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              data-testid="tool-form-cancel"
            >
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || form.name.trim() === ""}
              data-testid="tool-form-submit"
            >
              {mutation.isPending ? t("saving") : isEdit ? t("saveChanges") : t("createTool")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation
// ---------------------------------------------------------------------------
export function DeleteToolDialog({ tool, onClose }: { tool: CatalogTool; onClose: () => void }) {
  const queryClient = useQueryClient();
  const t = useT("tools");
  const errorText = useErrorText();
  const mutation = useMutation<void, ApiError, void>({
    mutationFn: () => apiFetch<void>(`/tools/${tool.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools-catalog"] });
      onClose();
    },
  });

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()} size="sm">
      <DialogContent data-testid="tool-delete-dialog">
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {t("deleteDescriptionPrefix")}
            <strong>{tool.name}</strong>
            {t("deleteDescriptionSuffix")}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="tool-delete-cancel">
            {t("cancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            data-testid="tool-delete-confirm"
          >
            <X className="mr-1 h-4 w-4" />
            {mutation.isPending ? t("deleting") : t("delete")}
          </Button>
        </DialogFooter>
        {mutation.isError && (
          <p
            className="text-danger-soft-foreground px-6 pb-4 text-xs"
            data-testid="tool-delete-error"
          >
            {errorText(mutation.error)}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
