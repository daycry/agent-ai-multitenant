"use client";

/**
 * Alta y edición de UNA configuración de webhook entrante.
 *
 * Sale de `page.tsx` en prod-16 `task_prod16_03`: al migrar el módulo al
 * diccionario la pantalla cruzó las 800 líneas que vigila
 * `check-component-size`, y el diálogo es la costura natural — no comparte
 * estado con la lista, sólo el `onSubmit`.
 *
 * Una regla que no se ve en el render y conviene no perder: los templates y el
 * `target_task_id` en blanco viajan como `null`, no como cadena vacía, porque el
 * validador del backend trata «ausente» y «vacío» distinto.
 */

import { useState } from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/lib/i18n";

import {
  ACTIONS,
  emptyConfigForm,
  emptyRule,
  ORIGINS,
  type ActionKind,
  type ActionMappingRule,
  type Origin,
  type WebhookConfig,
} from "./webhook-types";

export function WebhookDialog({
  open,
  onOpenChange,
  initial,
  submitting,
  onSubmit,
  backendError,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: WebhookConfig | null;
  submitting: boolean;
  onSubmit: (form: ReturnType<typeof emptyConfigForm>) => void;
  backendError: string | null;
}) {
  const t = useT("incomingWebhooks");
  const isEdit = initial !== null;
  const [state, setState] = useState<ReturnType<typeof emptyConfigForm>>(() =>
    initial
      ? {
          origin: initial.origin,
          name: initial.name,
          enabled: initial.enabled,
          action_mappings: initial.action_mappings.map((r) => ({ ...r })),
        }
      : emptyConfigForm(),
  );

  function updateRule(index: number, patch: Partial<ActionMappingRule>) {
    setState((s) => ({
      ...s,
      action_mappings: s.action_mappings.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    }));
  }

  function addRule() {
    setState((s) => ({ ...s, action_mappings: [...s.action_mappings, emptyRule()] }));
  }

  function removeRule(index: number) {
    setState((s) => ({
      ...s,
      action_mappings: s.action_mappings.filter((_, i) => i !== index),
    }));
  }

  function handleSubmit() {
    // Normalise blank templates / target ids to null so the backend validator
    // treats them as absent (mirrors the rule contract).
    const cleaned: ActionMappingRule[] = state.action_mappings.map((r) => ({
      event_type: r.event_type.trim() || "*",
      action: r.action,
      title_template: r.title_template?.trim() ? r.title_template.trim() : null,
      body_template: r.body_template?.trim() ? r.body_template.trim() : null,
      target_task_id:
        r.action === "create_task"
          ? null
          : r.target_task_id?.trim()
            ? r.target_task_id.trim()
            : null,
    }));
    onSubmit({ ...state, name: state.name.trim(), action_mappings: cleaned });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="webhook-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("dialogEditTitle") : t("dialogCreateTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            <div>
              <Label htmlFor="webhook-form-origin">{t("originLabel")}</Label>
              <select
                id="webhook-form-origin"
                data-testid="webhook-form-origin"
                className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm disabled:opacity-60"
                value={state.origin}
                disabled={isEdit}
                onChange={(e) => setState({ ...state, origin: e.target.value as Origin })}
              >
                {ORIGINS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </option>
                ))}
              </select>
              {isEdit ? (
                <p className="text-muted-foreground mt-1 text-xs">{t("originLockedHint")}</p>
              ) : null}
            </div>

            <div>
              <Label htmlFor="webhook-form-name">{t("nameLabel")}</Label>
              <Input
                id="webhook-form-name"
                data-testid="webhook-form-name"
                value={state.name}
                onChange={(e) => setState({ ...state, name: e.target.value })}
                placeholder={t("namePlaceholder")}
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="webhook-form-enabled"
                checked={state.enabled}
                onChange={(e) => setState({ ...state, enabled: e.target.checked })}
              />
              {t("enabledLabel")}
            </label>

            {/* Mappings editor */}
            <div className="border-t pt-3">
              <div className="mb-2 flex items-center justify-between">
                <Label>{t("mappingsLabel")}</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addRule}
                  data-testid="webhook-form-add-rule"
                >
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  {t("addMapping")}
                </Button>
              </div>
              {state.action_mappings.length === 0 ? (
                <p
                  className="text-muted-foreground text-xs italic"
                  data-testid="webhook-form-rules-empty"
                >
                  {t("mappingsEmpty")}
                </p>
              ) : (
                <ul className="space-y-3" data-testid="webhook-form-rules">
                  {state.action_mappings.map((rule, idx) => (
                    <li key={idx} className="bg-muted/30 rounded-md border p-3">
                      <div className="flex items-center gap-2">
                        <Input
                          aria-label="event_type"
                          data-testid={`webhook-form-rule-event-${idx}`}
                          value={rule.event_type}
                          onChange={(e) => updateRule(idx, { event_type: e.target.value })}
                          placeholder="github.pull_request_review"
                          className="flex-1"
                        />
                        <select
                          aria-label="action"
                          data-testid={`webhook-form-rule-action-${idx}`}
                          className="border-input bg-background h-10 rounded-md border px-2 text-sm"
                          value={rule.action}
                          onChange={(e) =>
                            updateRule(idx, { action: e.target.value as ActionKind })
                          }
                        >
                          {ACTIONS.map((a) => (
                            <option key={a.value} value={a.value}>
                              {t(a.labelKey)}
                            </option>
                          ))}
                        </select>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => removeRule(idx)}
                          data-testid={`webhook-form-rule-remove-${idx}`}
                          aria-label={t("removeMapping")}
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      {rule.action !== "create_task" ? (
                        <Input
                          aria-label="target_task_id"
                          data-testid={`webhook-form-rule-target-${idx}`}
                          value={rule.target_task_id ?? ""}
                          onChange={(e) => updateRule(idx, { target_task_id: e.target.value })}
                          placeholder={t("targetTaskPlaceholder")}
                          className="mt-2"
                        />
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="webhook-form-backend-error"
              >
                {backendError}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid="webhook-form-cancel"
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || !state.name.trim()}
            data-testid="webhook-form-submit"
          >
            {submitting ? t("saving") : isEdit ? t("saveChanges") : t("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
