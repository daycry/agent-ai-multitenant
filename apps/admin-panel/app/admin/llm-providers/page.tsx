"use client";

/**
 * task_11_2_05 — Pantalla 'Proveedores LLM' del System Admin (ADR 0028).
 *
 * Los proveedores LLM (los cuatro caminos cerrados del ADR 0021:
 * `claude_sdk` / `copilot` / `azure_foundry` / `ollama`) son
 * **platform-global** y los gestiona EXCLUSIVAMENTE el System Admin — no
 * tienen `tenant_id` ni RLS; el backend gatea cada endpoint con
 * `require_system_admin` sobre la sesión BYPASSRLS. Esta pantalla deja al
 * System Admin:
 *
 *   - Listar los proveedores con su `kind` + `display_name` + estado
 *     (`is_active`) + si tienen credencial guardada (`has_credential`).
 *   - "Probar conexión" por proveedor (POST `/{id}/test`) → ok/err
 *     clasificado, sin filtrar nunca el secreto.
 *   - Crear / editar un proveedor: los campos de credencial cambian según
 *     el `kind`. Los inputs de secreto son **write-only**: al editar nunca
 *     se muestra el valor — solo "configurado"; dejarlo vacío conserva el
 *     secreto actual en Vault.
 *   - Activar / desactivar (toggle) sin abrir el diálogo.
 *   - GitHub Copilot Device Flow: iniciar (start) → mostrar `user_code` +
 *     `verification_uri` + abrir enlace → hacer polling (poll) hasta que
 *     GitHub autorice; el token acuñado va SOLO a Vault (nunca a la UI).
 *
 * SECRETOS SOLO A VAULT (CLAUDE.md / ADR 0028 — innegociable): el endpoint
 * recibe la credencial como `SecretStr`, la escribe en Vault
 * (`platform/llm/<id>`) y persiste solo `secret_vault_path`. Ninguna
 * respuesta de la API devuelve jamás el valor del secreto; esta pantalla
 * NUNCA lo muestra (solo el booleano `has_credential`).
 *
 * Endpoints backend:
 *   GET    /admin/llm-providers                       — list (newest first)
 *   POST   /admin/llm-providers                       — create (201)
 *   GET    /admin/llm-providers/{id}                  — one
 *   PUT    /admin/llm-providers/{id}                  — update / rotar credencial
 *   DELETE /admin/llm-providers/{id}                  — delete + borra secret de Vault
 *   POST   /admin/llm-providers/{id}/test             — probar conexión
 *   POST   /admin/llm/copilot/device-flow/start       — iniciar device flow
 *   POST   /admin/llm/copilot/device-flow/poll        — un poll del device flow
 *
 * **Partición** (prod-16 `task_prod16_08`): esta pantalla tenía 996 líneas y
 * todo el texto cableado en castellano. El cuerpo vive ahora en
 * `providers-table.tsx`, y de él cuelgan `provider-form-dialog.tsx` y
 * `copilot-device-flow-dialog.tsx`, con los tipos comunes en
 * `llm-provider-types.ts`. Aquí sólo quedan la cabecera y el gate de rol.
 */

import { Cpu, ShieldAlert } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { useT } from "@/lib/i18n";

import { LlmProvidersContent } from "./providers-table";

export default function LlmProvidersPage() {
  const t = useT("llmProviders");

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="llm-providers-page"
    >
      <PageHeader
        icon={<Cpu className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="llm-providers-header"
      />
      {/* La pantalla completa es solo System Admin: el backend gatea con
          require_system_admin y la tabla no tiene tenant_id ni RLS. */}
      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6" data-testid="llm-providers-forbidden">
            <CardContent className="flex items-center gap-3 py-10">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
            </CardContent>
          </Card>
        }
      >
        <LlmProvidersContent />
      </RoleGuard>
    </div>
  );
}
