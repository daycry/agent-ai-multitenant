"use client";

/**
 * Filtros del catálogo Modelos & Precios.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_06` (la pantalla tenía 514
 * líneas y el objetivo son < 400). Corte verbatim: mismos `data-testid` y misma
 * semántica de "aplicar al enviar".
 *
 * El estado del formulario vive AQUÍ y sólo sale por `onApply`. Eso es lo que
 * evita que teclear en un input refetchee en cada pulsación — era así antes del
 * troceo y sigue siéndolo: `page.tsx` únicamente guarda lo ya aplicado.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useT } from "@/lib/i18n";

import { MODALITIES, type LlmProvider, type Modality } from "./model-price-types";

export interface AppliedPriceFilters {
  provider: string;
  modelId: string;
  modality: "" | Modality;
  providerId: string;
  currentOnly: boolean;
}

/** Estado inicial y también el que deja "Limpiar". */
export const EMPTY_FILTERS: AppliedPriceFilters = {
  provider: "",
  modelId: "",
  modality: "",
  providerId: "",
  currentOnly: true,
};

export function PriceFilters({
  providers,
  isSystemAdmin,
  onApply,
}: {
  providers: LlmProvider[];
  isSystemAdmin: boolean;
  onApply: (filters: AppliedPriceFilters) => void;
}) {
  const t = useT("modelPrices");
  const [provider, setProvider] = useState("");
  const [modelId, setModelId] = useState("");
  const [modality, setModality] = useState<"" | Modality>("");
  const [providerId, setProviderId] = useState("");
  const [currentOnly, setCurrentOnly] = useState(true);

  return (
    <Card className="mt-6" data-testid="price-filters">
      <CardContent className="grid grid-cols-1 gap-3 pt-5 sm:grid-cols-2 lg:grid-cols-6 lg:items-end">
        <div className="space-y-1">
          <Label htmlFor="filter-provider">{t("filterFamily")}</Label>
          <Input
            id="filter-provider"
            placeholder="anthropic"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            data-testid="filter-provider"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-model">{t("filterModel")}</Label>
          <Input
            id="filter-model"
            placeholder="claude-sonnet-4-5"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            data-testid="filter-model"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-modality">{t("filterModality")}</Label>
          <Select
            id="filter-modality"
            value={modality}
            onChange={(e) => setModality(e.target.value as "" | Modality)}
            data-testid="filter-modality"
          >
            <option value="">{t("filterAllModalities")}</option>
            {MODALITIES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        </div>
        {/* task_11_2_06 — filter by associated platform provider. Only
            the System Admin can read the providers list, so this select
            is shown to them; a tenant reader still has the other filters. */}
        {isSystemAdmin ? (
          <div className="space-y-1">
            <Label htmlFor="filter-provider-id">{t("filterProvider")}</Label>
            <Select
              id="filter-provider-id"
              value={providerId}
              onChange={(e) => setProviderId(e.target.value)}
              data-testid="filter-provider-id"
            >
              <option value="">{t("filterAllProviders")}</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        <div className="flex items-center gap-2 pb-2">
          <Checkbox
            id="filter-current-only"
            checked={currentOnly}
            onChange={(e) => setCurrentOnly(e.target.checked)}
            data-testid="filter-current-only"
          />
          <Label htmlFor="filter-current-only">{t("filterCurrentOnly")}</Label>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() =>
              onApply({
                provider: provider.trim(),
                modelId: modelId.trim(),
                modality,
                providerId,
                currentOnly,
              })
            }
            data-testid="filter-apply"
          >
            {t("filterApply")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setProvider("");
              setModelId("");
              setModality("");
              setProviderId("");
              setCurrentOnly(true);
              onApply(EMPTY_FILTERS);
            }}
            data-testid="filter-reset"
          >
            {t("filterReset")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
