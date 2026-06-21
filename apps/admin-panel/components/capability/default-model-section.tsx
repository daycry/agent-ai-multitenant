"use client";

/**
 * Sección reutilizable "Modelo por defecto" para Equipo y Proyecto (Ola A-UI /
 * ADR 0065). Reusa el selector puro `PersonaModelFields` + los helpers de
 * `persona.ts`. Modela la semántica de HERENCIA: un checkbox "heredar" que, al
 * estar marcado, envía `model_config = {}` (hereda del nivel superior:
 * proyecto → plataforma); al desmarcarlo, el operador pinea provider+modelo.
 *
 * El padre cablea la mutación (PUT /teams/{id} o PUT /projects/{id}); este
 * componente solo decide el `model_config` a enviar y lo pasa por `onSave`.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { PersonaModelFields } from "@/components/capability/persona-section";
import { useLang } from "@/lib/lang-context";
import {
  buildModelConfig,
  draftFromConfig,
  validateDraft,
  type ModelConfig,
  type ModelConfigDraft,
} from "@/lib/persona/persona";

export function DefaultModelSection({
  value,
  onSave,
  pending = false,
  isReadOnly = false,
  idPrefix,
  scopeLabel,
  title,
  description,
}: {
  /** model_config actual de la entidad (equipo/proyecto). `{}`/null = hereda. */
  value: ModelConfig | null | undefined;
  /** Cablea la persistencia (PUT con `{ model_config }`). */
  onSave: (modelConfig: ModelConfig) => void;
  pending?: boolean;
  /** Built-in / sin permiso → solo lectura (muestra, no edita). */
  isReadOnly?: boolean;
  idPrefix: string;
  /** "del proyecto" | "del equipo" para el copy bilingüe. */
  scopeLabel: { es: string; en: string };
  /** Título opcional (default: "Modelo por defecto {scopeLabel}"). */
  title?: { es: string; en: string };
  /** Descripción opcional (default: copy de herencia del modelo por defecto). */
  description?: { es: string; en: string };
}) {
  const { lang } = useLang();
  const t = (es: string, en: string) => (lang === "es" ? es : en);
  const pinned = Boolean(value?.provider && value?.model);

  const [inherit, setInherit] = useState(!pinned);
  const [draft, setDraft] = useState<ModelConfigDraft>(() => draftFromConfig(value));
  const errors = inherit ? [] : validateDraft(draft, lang);

  function handleSave() {
    onSave(inherit ? {} : buildModelConfig({ current: value ?? null, draft, prompts: {} }));
  }

  return (
    <Card data-testid={`${idPrefix}-default-model`}>
      <CardHeader>
        <CardTitle>
          {title
            ? t(title.es, title.en)
            : t(`Modelo por defecto ${scopeLabel.es}`, `Default model ${scopeLabel.en}`)}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          {description
            ? t(description.es, description.en)
            : t(
                "Lo heredan los agentes sin modelo propio. Vacío = heredar del nivel superior " +
                  "(equipo → proyecto → plataforma).",
                "Inherited by agents without their own model. Empty = inherit from the level " +
                  "above (team → project → platform).",
              )}
        </p>

        {isReadOnly ? (
          <p
            className="text-muted-foreground text-sm"
            data-testid={`${idPrefix}-default-model-readonly`}
          >
            {pinned
              ? `${value?.provider} · ${value?.model}`
              : t("Hereda del nivel superior.", "Inherits from the level above.")}
          </p>
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={inherit}
                onChange={(e) => setInherit(e.target.checked)}
                data-testid={`${idPrefix}-inherit`}
              />
              {t("Heredar (sin modelo propio)", "Inherit (no own model)")}
            </label>

            {!inherit && (
              <PersonaModelFields draft={draft} onChange={setDraft} idPrefix={idPrefix} />
            )}

            <Button
              type="button"
              onClick={handleSave}
              disabled={pending || (!inherit && errors.length > 0)}
              data-testid={`${idPrefix}-save`}
            >
              {pending ? t("Guardando…", "Saving…") : t("Guardar modelo", "Save model")}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
