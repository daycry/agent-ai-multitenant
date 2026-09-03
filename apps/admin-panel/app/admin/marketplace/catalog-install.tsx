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
 * **El destino de la navegación sale de la respuesta.** Un listing cuyo nivel
 * de confianza exige consentimiento nace `disabled` con cero permisos otorgados
 * (`api_server/marketplace/install.py:367-374`): ahí falta un paso obligatorio y
 * se lleva al operador a otorgarlos. Uno `verified` nace `enabled`, y entonces
 * lo siguiente no es consentir —no hay nada que consentir— sino **desplegar**,
 * que es lo que ofrece la ficha de la instalación. Instalar no es desplegar
 * (ADR 0142): llevar siempre a la pantalla de permisos dejaría al operador en
 * una página vacía creyendo que ya está.
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
 * A dónde se va tras instalar. `disabled` recién creada significa «el nivel de
 * confianza exige consentimiento y aún no se ha dado»; cualquier otro estado
 * significa que la instalación ya está viva y lo pendiente es desplegarla.
 */
export function destinoTrasInstalar(installation: InstallacionCreada): string {
  const ficha = `/admin/marketplace/installations/${installation.id}`;
  return installation.status === "disabled" ? `${ficha}/permissions` : ficha;
}

export function CatalogInstallButton({
  listingId,
  installed,
}: {
  listingId: string;
  installed: boolean;
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
      router.push(destinoTrasInstalar(installation));
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
