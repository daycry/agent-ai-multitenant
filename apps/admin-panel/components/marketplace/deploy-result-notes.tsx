"use client";

/**
 * Lo que un despliegue del marketplace dijo de sí mismo, pintado igual en las
 * dos puertas (`task_mk_11`, UI-02): la ficha de la instalación
 * (`deployments-section.tsx`) y la pestaña MCP del proyecto
 * (`available-capabilities-section.tsx`). Antes la segunda descartaba
 * `warnings` y `oauth_pending`, y ninguna de las dos pintaba `created_refs`:
 * un despliegue que no entregó nada a nadie salía como un 201 limpio.
 *
 * Tres bloques, cada uno sólo si hay algo que decir:
 *   - avisos del backend (texto tal cual: traen el nombre concreto de la tool,
 *     el servidor o el rol);
 *   - OAuth pendiente («Conectar» en la pestaña MCP);
 *   - qué recibió quién (`created_refs`): servidores MCP declarados, política
 *     rol→tool escrita, import encolado, agentes que recibieron la tool o la
 *     skill. Las claves conocidas llevan etiqueta traducida; una clave nueva del
 *     backend se pinta cruda para no esconderla.
 * Y un cuarto, derivado: si un aviso habla de un tipo diferido (ADR 0081), se
 * añade la explicación traducida con el enlace a «Instaladas», donde
 * `task_mk_10` ya pinta «Autorizada, sin capacidad».
 */

import Link from "next/link";

import { useT } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n";

const REF_LABEL: Record<string, MessageKey<"marketplaceDeploy">> = {
  mcp_servers: "refMcpServers",
  mcp_tool_roles: "refMcpToolRoles",
  mcp_import: "refMcpImport",
  agent_tools: "refAgentTools",
  agent_skills: "refAgentSkills",
};

/** `created_refs` → líneas `[clave, detalle]`; puro para poder testearlo solo. */
export function summariseCreatedRefs(
  refs: Record<string, unknown> | null | undefined,
): [string, string][] {
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(refs ?? {})) {
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      const items = value.map((v) =>
        typeof v === "string"
          ? v
          : typeof v === "object" && v !== null
            ? JSON.stringify(v)
            : String(v),
      );
      out.push([key, items.join(", ")]);
    } else if (typeof value === "object" && value !== null) {
      const record = value as Record<string, unknown>;
      out.push([
        key,
        Object.entries(record)
          .map(([k, v]) => `${k}=${String(v)}`)
          .join(" · "),
      ]);
    } else if (value !== null && value !== undefined && value !== "") {
      out.push([key, String(value)]);
    }
  }
  return out;
}

/** Heurística deliberada: el backend no tipa este aviso; el texto sí lo nombra. */
export function mentionsDeferredType(warnings: readonly string[]): boolean {
  return warnings.some((w) => /diferid|deferred/i.test(w));
}

export function DeployResultNotes({
  warnings,
  oauthPending,
  createdRefs,
  testId,
}: {
  warnings: readonly string[];
  oauthPending: boolean;
  createdRefs?: Record<string, unknown> | null;
  /** Prefijo de `data-testid`: `deploy-warnings-<x>`, `deploy-oauth-<x>`, `deploy-created-refs-<x>`. */
  testId: string;
}) {
  const t = useT("marketplaceDeploy");
  const refs = summariseCreatedRefs(createdRefs);
  return (
    <div className="space-y-1 text-xs">
      {warnings.length > 0 ? (
        <ul
          className="text-warning-soft-foreground space-y-0.5 pl-4"
          data-testid={`deploy-warnings-${testId}`}
        >
          {warnings.map((warning, index) => (
            <li key={index}>• {warning}</li>
          ))}
        </ul>
      ) : null}
      {mentionsDeferredType(warnings) ? (
        <p className="text-warning-soft-foreground pl-4" data-testid={`deploy-deferred-${testId}`}>
          {t("deferredTypeNote")}{" "}
          <Link href="/admin/marketplace" className="underline">
            {t("deferredTypeLink")}
          </Link>
        </p>
      ) : null}
      {oauthPending ? (
        <p className="text-warning-soft-foreground pl-4" data-testid={`deploy-oauth-${testId}`}>
          {t("oauthPending")}
        </p>
      ) : null}
      {refs.length > 0 ? (
        <div className="pl-4" data-testid={`deploy-created-refs-${testId}`}>
          <p className="text-muted-foreground">{t("createdRefsTitle")}</p>
          <ul className="space-y-0.5">
            {refs.map(([key, detail]) => (
              <li key={key}>
                <span className="font-medium">{REF_LABEL[key] ? t(REF_LABEL[key]) : key}</span>:{" "}
                {detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
