"use client";

/**
 * Alta / edicion de un precio de modelo, en USD canonico.
 *
 * Extraido de `model-price-dialogs.tsx` en prod-16 `task_prod16_06`. Corte
 * verbatim: mismos `data-testid`, misma validacion y mismo cuerpo del POST/PUT.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import {
  MODALITIES,
  SOURCES,
  UNIT_KEY,
  UNITS,
  type Modality,
  type ModelPrice,
  type Source,
  type Unit,
} from "./model-price-types";
// ===========================================================================
// Create / edit dialog (System Admin only — wrapped at the call site too)
// ===========================================================================
interface PriceFormDialogProps {
  mode: "create" | "edit";
  price?: ModelPrice;
  onClose: () => void;
  onSaved: () => void;
}

export function PriceFormDialog({ mode, price, onClose, onSaved }: PriceFormDialogProps) {
  const t = useT("modelPrices");
  const errorText = useErrorText();
  const isEdit = mode === "edit";

  // The catalog key (provider/model_id/modality) is immutable on edit.
  const [provider, setProvider] = useState(price?.provider ?? "");
  const [modelId, setModelId] = useState(price?.model_id ?? "");
  const [modality, setModality] = useState<Modality>((price?.modality as Modality) ?? "text");
  const [inputPrice, setInputPrice] = useState(price?.input_price ?? "");
  const [outputPrice, setOutputPrice] = useState(price?.output_price ?? "");
  const [cachedInputPrice, setCachedInputPrice] = useState(price?.cached_input_price ?? "");
  const [unit, setUnit] = useState<Unit>((price?.unit as Unit) ?? "per_1m_tokens");
  const [contextWindow, setContextWindow] = useState(
    price?.context_window != null ? String(price.context_window) : "",
  );
  const [source, setSource] = useState<Source>((price?.source as Source) ?? "manual");

  const saveMutation = useMutation({
    mutationFn: () => {
      if (isEdit && price) {
        // PATCH only the mutable fields. The key is immutable.
        const body: Record<string, unknown> = {
          input_price: inputPrice,
          output_price: outputPrice,
          cached_input_price: cachedInputPrice.trim() === "" ? null : cachedInputPrice,
          unit,
          context_window: contextWindow.trim() === "" ? null : Number(contextWindow),
          source,
        };
        return apiFetch<ModelPrice>(`/admin/model-prices/${price.id}`, {
          method: "PATCH",
          body,
        });
      }
      // USD-canonical: no `currency` on the wire.
      const body: Record<string, unknown> = {
        provider: provider.trim(),
        model_id: modelId.trim(),
        modality,
        input_price: inputPrice,
        output_price: outputPrice,
        cached_input_price: cachedInputPrice.trim() === "" ? null : cachedInputPrice,
        unit,
        context_window: contextWindow.trim() === "" ? null : Number(contextWindow),
        source,
      };
      return apiFetch<ModelPrice>("/admin/model-prices", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  const canSave =
    inputPrice.trim() !== "" &&
    outputPrice.trim() !== "" &&
    (isEdit || (provider.trim() !== "" && modelId.trim() !== ""));

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogContent data-testid="price-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("formEditTitle") : t("formCreateTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs" data-testid="price-form-usd-note">
            {t("formUsdNote", { unit: UNIT_KEY[unit] ? t(UNIT_KEY[unit]) : unit })}
          </p>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-provider">{t("fieldProvider")}</Label>
              <Input
                id="form-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                disabled={isEdit}
                placeholder="anthropic"
                data-testid="form-provider"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-model">{t("colModel")}</Label>
              <Input
                id="form-model"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                disabled={isEdit}
                placeholder="claude-sonnet-4-5"
                data-testid="form-model"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-modality">{t("colModality")}</Label>
              <Select
                id="form-modality"
                value={modality}
                onChange={(e) => setModality(e.target.value as Modality)}
                disabled={isEdit}
                data-testid="form-modality"
              >
                {MODALITIES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-input">{t("fieldInput")}</Label>
              <Input
                id="form-input"
                type="number"
                min={0}
                step="any"
                value={inputPrice}
                onChange={(e) => setInputPrice(e.target.value)}
                data-testid="form-input-price"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-output">{t("fieldOutput")}</Label>
              <Input
                id="form-output"
                type="number"
                min={0}
                step="any"
                value={outputPrice}
                onChange={(e) => setOutputPrice(e.target.value)}
                data-testid="form-output-price"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-cached">{t("fieldCached")}</Label>
              <Input
                id="form-cached"
                type="number"
                min={0}
                step="any"
                value={cachedInputPrice}
                onChange={(e) => setCachedInputPrice(e.target.value)}
                placeholder="~10% input"
                data-testid="form-cached-price"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-unit">{t("colUnit")}</Label>
              <Select
                id="form-unit"
                value={unit}
                onChange={(e) => setUnit(e.target.value as Unit)}
                data-testid="form-unit"
              >
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {UNIT_KEY[u] ? t(UNIT_KEY[u]) : u}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-context">{t("fieldContextWindow")}</Label>
              <Input
                id="form-context"
                type="number"
                min={1}
                step={1}
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                placeholder="200000"
                data-testid="form-context-window"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-source">{t("colSource")}</Label>
              <Select
                id="form-source"
                value={source}
                onChange={(e) => setSource(e.target.value as Source)}
                data-testid="form-source"
              >
                {SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          {saveMutation.isError ? (
            <p className="text-destructive text-xs" data-testid="price-form-error">
              {errorText(saveMutation.error)}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="price-form-cancel">
            {t("cancel")}
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!canSave || saveMutation.isPending}
            data-testid="price-form-submit"
          >
            {saveMutation.isPending ? t("saving") : isEdit ? t("save") : t("submitCreate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
