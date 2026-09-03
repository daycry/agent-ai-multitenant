"use client";

/**
 * Instalar una capacidad desde el catálogo (`task_mk_00`).
 *
 * Hasta la auditoría del 2026-09-02 el panel **no emitía un solo**
 * `POST /marketplace/installations`: la pestaña «Instaladas» presuponía
 * instalaciones que ninguna pantalla sabía crear, así que la única forma de
 * instalar era llamar a la API a mano. La tarjeta del catálogo era informativa
 * y punto.
 *
 * ## Dos decisiones, y por qué
 *
 * **La petición va síncrona.** El endpoint admite `async_gates`, que devuelve
 * `202` y deja la fila en `analyzing` hasta que un worker de la lane
 * `marketplace` la atienda. En una instalación que no levante esa lane,
 * `analyzing` es permanente y la UI ya habría dicho «aceptada»: exactamente el
 * verde que miente contra el que va este plan. Se manda sin `async_gates`, se
 * espera el estado final, y si una puerta de seguridad falla el backend
 * responde 422 y se lee aquí mismo.
 *
 * **El destino de la navegación sale de la respuesta Y del listing.** Un listing
 * cuyo nivel de confianza exige consentimiento nace `disabled` con cero permisos
 * otorgados (`api_server/marketplace/install.py:367-374`); si además pide
 * permisos, ahí falta un paso obligatorio y se lleva al operador a otorgarlos.
 * Uno `verified` nace `enabled`, y entonces lo siguiente no es consentir —no hay
 * nada que consentir— sino **desplegar**, que es lo que ofrece la ficha de la
 * instalación. Instalar no es desplegar (ADR 0142).
 *
 * El tercer caso lo encontró la revisión de dos lentes y es el que obliga a
 * mirar también el listing: `needs_consent` depende SÓLO del nivel de confianza
 * (`marketplace/finalize.py:79-85`), así que un listing `community` que no pide
 * ningún permiso —toda publicación privada nace `community`, y un `SKILL.md` sin
 * bloque `permissions` pide cero— también nace `disabled`. Llevarlo a la
 * pantalla de permisos lo dejaría exactamente donde este comentario dice que no
 * hay que dejarlo: en una página vacía, sin botón que pulsar. Se lleva a la
 * ficha, que sí dice el estado.
 *
 * Que esa instalación no se pueda habilitar es un callejón del backend
 * ANTERIOR a este botón —`ConsentDecisionRequest.decisions` exige `min_length=1`,
 * así que ni siquiera se puede confirmar un consentimiento vacío— y está
 * registrado como MK-17 en el plan. Aquí no se disimula: no se navega a una
 * pantalla que no puede resolver nada.
 *
 * Este fichero vive aparte de `page.tsx` a propósito: esa pantalla está en 728
 * líneas de un techo de 800 que vigila `scripts/check-component-size.test.ts`
 * sobre el árbol real.
 */

import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";

import { INSTALLATIONS_KEY } from "./catalog-updates";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface InstallacionCreada {
  id: string;
  status: string;
}

/**
 * A dónde se va tras instalar. Sólo a la pantalla de permisos cuando de verdad
 * hay algo que otorgar: `disabled` recién creada Y el listing pide permisos.
 * En cualquier otro caso, la ficha — que es donde se ve el estado y se despliega.
 */
export function destinoTrasInstalar(
  installation: InstallacionCreada,
  { pidePermisos }: { pidePermisos: boolean },
): string {
  const ficha = `/admin/marketplace/installations/${installation.id}`;
  return installation.status === "disabled" && pidePermisos ? `${ficha}/permissions` : ficha;
}

export function CatalogInstallButton({
  listingId,
  installed,
  pidePermisos,
}: {
  listingId: string;
  installed: boolean;
  /** El listing declara permisos que otorgar (`requested_permissions`). */
  pidePermisos: boolean;
}) {
  const t = useT("marketplace");
  const errorText = useErrorText();
  const router = useRouter();
  const queryClient = useQueryClient();

  const install = useMutation({
    mutationFn: () =>
      apiFetch<InstallacionCreada>("/marketplace/installations", {
        method: "POST",
        body: { listing_id: listingId },
      }),
    onSuccess: (installation) => {
      void queryClient.invalidateQueries({ queryKey: INSTALLATIONS_KEY });
      router.push(destinoTrasInstalar(installation, { pidePermisos }));
    },
  });

  if (installed) {
    return (
      <Badge variant="success" data-testid={`catalog-installed-${listingId}`}>
        {t("installed")}
      </Badge>
    );
  }

  return (
    <RoleGuard min="tenant_admin">
      <div className="flex shrink-0 flex-col items-end gap-1">
        <Button
          size="sm"
          onClick={() => install.mutate()}
          disabled={install.isPending}
          data-testid={`catalog-install-${listingId}`}
        >
          <Download className="mr-1 h-4 w-4" aria-hidden />
          {install.isPending ? t("installing") : t("install")}
        </Button>
        {install.error ? (
          <p
            className="text-destructive max-w-[16rem] text-right text-xs"
            data-testid={`catalog-install-error-${listingId}`}
          >
            {errorText(install.error)}
          </p>
        ) : null}
      </div>
    </RoleGuard>
  );
}
