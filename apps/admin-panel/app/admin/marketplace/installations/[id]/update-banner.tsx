"use client";

/**
 * El banner «v X.Y disponible» de la ficha de instalación — `task_mkt2_12`.
 *
 * ## Por qué existe este fichero y por qué llegó tarde
 *
 * El endpoint `GET /installations/{id}/update-check` y el `POST …/update` se
 * entregaron completos y con tests, y la casilla se dio por cerrada. Pero la
 * tarea pedía además esto — «UI: banner "v X.Y disponible" en ficha y catálogo
 * con el diff de permisos en claro»— y sin ello el mecanismo era invisible:
 * ningún administrador iba a descubrir por su cuenta que su instalación se
 * había quedado atrás. Es el modo de fallo que la auditoría de esta base
 * bautizó como «falla el cableado del último tramo, no el diseño».
 *
 * El backend ya venía preparado: `permission_delta` y `requires_consent` en la
 * respuesta de `update-check` existen precisamente para pintarse aquí.
 *
 * La mitad del CATÁLOGO llegó después (`../../catalog-updates.tsx`), y con ella
 * los tipos y predicados se mudaron a `components/marketplace/update-check.ts`:
 * los dos avisos tienen que estar de acuerdo sobre si una actualización pide
 * más permisos, y dos copias de esa aritmética no lo estarían mucho tiempo.
 *
 * ## Las tres reglas que hacen que el banner no mienta
 *
 * 1. **El delta se enseña ANTES de ofrecer el botón, no después.** Actualizar
 *    puede ampliar lo que la capacidad puede hacer; un botón «Actualizar» sin
 *    el delta a la vista es un consentimiento arrancado a ciegas.
 * 2. **Un salto de MAJOR no se ofrece como un clic más.** Se dice que existe y
 *    se exige el opt-in explícito, porque semver no promete compatibilidad
 *    cruzando esa frontera.
 * 3. **Si hace falta re-consentimiento, el botón lo dice** (`Revisar y
 *    actualizar`, no `Actualizar`): prometer un clic y contestar un 409 con una
 *    lista de permisos es la peor UI posible.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  awaitsMajorOptIn,
  deltaWidens,
  hasUpdate,
  pendingTypes,
  proposedVersion,
  requiresConsent,
  updateCheckKey,
  updateCheckPath,
  type UpdateCheck,
} from "@/components/marketplace/update-check";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

export function UpdateBanner({ installationId }: { installationId: string }) {
  const t = useT("marketplaceDeploy");
  const qc = useQueryClient();
  const errorText = useErrorText();
  const [error, setError] = useState<string | null>(null);
  const [allowMajor, setAllowMajor] = useState(false);

  const check = useQuery<UpdateCheck>({
    queryKey: updateCheckKey(installationId, allowMajor),
    queryFn: () => apiFetch<UpdateCheck>(updateCheckPath(installationId, allowMajor)),
  });

  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch(`/marketplace/installations/${installationId}/update`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["marketplace-update-check", installationId] });
      void qc.invalidateQueries({ queryKey: ["marketplace-installation", installationId] });
      void qc.invalidateQueries({ queryKey: ["marketplace-deployments", installationId] });
    },
    onError: (err: unknown) => setError(errorText(err)),
  });

  const data = check.data;
  // Nada que ofrecer: ni banner. Una franja permanente diciendo «estás al día»
  // enseña a ignorar la franja, y el día que diga algo tampoco se leerá.
  //
  // La condición es `hasUpdate`, NO `update_available`: éste último es
  // `target_version is not None` en el backend, así que un salto de MAJOR
  // pendiente del opt-in lo trae en `false`. Gatear por él escondía el caso
  // que la regla 2 dice cubrir — el banner no se pintaba y el opt-in de major
  // no llegaba a existir en pantalla.
  if (!data || !hasUpdate(data)) return null;

  const delta = data.permission_delta;
  const pending = pendingTypes(delta);
  const needsConsent = requiresConsent(data);
  // Un salto de major solo se propone si YA se pidió el opt-in; si no, se
  // anuncia que existe y se ofrece el interruptor.
  const majorOnly = awaitsMajorOptIn(data);

  const consentAll = () => Object.fromEntries(pending.map((type) => [type, "grant"]));

  return (
    <div
      className="border-warning/40 bg-warning/10 flex flex-col gap-3 rounded-lg border p-4"
      data-testid="update-banner"
    >
      <div className="flex flex-wrap items-center gap-2">
        <ArrowUpCircle className="text-warning h-5 w-5 shrink-0" />
        <span className="font-medium" data-testid="update-banner-headline">
          {t("updateAvailable", {
            version: proposedVersion(data),
            installed: data.installed_version,
          })}
        </span>
        {data.latest_is_major_bump ? (
          <Badge variant="warning" data-testid="update-banner-major">
            {t("updateMajor")}
          </Badge>
        ) : null}
      </div>

      {/* Regla 1: el delta va ANTES del botón, siempre. */}
      {deltaWidens(delta) ? (
        <div className="flex flex-col gap-1 text-sm" data-testid="update-banner-delta">
          <span className="flex items-center gap-1.5 font-medium">
            <AlertTriangle className="h-4 w-4" />
            {t("updateAsksMore")}
          </span>
          <ul className="list-disc pl-5">
            {delta?.added.map((p) => (
              <li key={`a-${p.type}`} data-testid={`update-delta-added-${p.type}`}>
                {t("updateDeltaAdded", { type: p.type })}
              </li>
            ))}
            {delta?.changed.map((c) => (
              <li key={`c-${c.type}`} data-testid={`update-delta-changed-${c.type}`}>
                {t("updateDeltaChanged", {
                  type: c.type,
                  from: JSON.stringify(c.from),
                  to: JSON.stringify(c.to),
                })}
              </li>
            ))}
          </ul>
        </div>
      ) : delta && delta.removed.length > 0 ? (
        <p className="text-muted-foreground text-sm" data-testid="update-banner-narrows">
          {t("updateAsksLess")}
        </p>
      ) : null}

      {error ? (
        <p className="text-danger text-sm" data-testid="update-banner-error">
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {majorOnly ? (
          // Regla 2: cruzar un major no es un clic más. Primero el opt-in.
          <Button
            size="sm"
            variant="outline"
            data-testid="update-banner-allow-major"
            onClick={() => setAllowMajor(true)}
          >
            {t("updateAllowMajor")}
          </Button>
        ) : (
          <Button
            size="sm"
            data-testid="update-banner-apply"
            disabled={update.isPending}
            onClick={() =>
              update.mutate({
                allow_major: allowMajor,
                ...(needsConsent ? { consent: consentAll() } : {}),
              })
            }
          >
            {/* Regla 3: el botón dice si va a haber que decidir algo. */}
            {needsConsent ? t("updateReviewAndApply") : t("updateApply")}
          </Button>
        )}
      </div>
    </div>
  );
}
