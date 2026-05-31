import type { Metadata } from "next";

import { CodeBlock, ExternalLink, PageIntro, SectionCard } from "../portal-ui";

export const metadata: Metadata = {
  title: "API Reference · Portal de desarrollador",
  description:
    "Contrato OpenAPI 3.1 de la API pública v1, Swagger UI, autenticación X-API-Token, scopes y rate limit.",
};

const ENDPOINTS: ReadonlyArray<[string, string, string]> = [
  ["/api/v1/projects", "GET / POST", "read / write"],
  ["/api/v1/projects/{project_id}", "GET", "read"],
  ["/api/v1/projects/{project_id}/plans", "GET / POST", "read / write"],
  ["/api/v1/plans/{plan_id}", "GET", "read"],
  ["/api/v1/projects/{project_id}/tasks", "GET / POST", "read / write"],
  ["/api/v1/projects/{project_id}/tasks/{task_id}", "GET", "read"],
  ["/api/v1/projects/{project_id}/conversations", "GET / POST", "read / write"],
  ["/api/v1/conversations/{conversation_id}", "GET", "read"],
  ["/api/v1/kbs", "GET / POST", "read / write"],
  ["/api/v1/kbs/{kb_id}", "GET", "read"],
];

const STATUS: ReadonlyArray<[string, string]> = [
  ["200 / 201", "Éxito (GET / POST creación)"],
  ["400", "X-API-Version pin no soportado"],
  ["401", "Token inválido o ausente"],
  ["403", "Token válido pero sin el scope requerido"],
  ["404", "Recurso de otro tenant o inexistente (nunca revela si un id existe)"],
  ["429", "Rate limit excedido (mira X-RateLimit-* / Retry-After)"],
];

export default function ApiReferencePage() {
  return (
    <div className="space-y-6">
      <PageIntro
        title="API Reference"
        lead="La API pública v1 (/api/v1) es una fachada fina y versionada sobre el dominio. El contrato OpenAPI 3.1 y el Swagger UI interactivo son públicos: léelos antes de acuñar tu primer token."
        testId="api-reference-intro"
      />

      <SectionCard title="Contrato OpenAPI 3.1 + Swagger UI" testId="api-reference-openapi">
        <p>
          El documento OpenAPI es autocontenido (solo rutas v1, <code>3.1.0</code> pineado, esquema
          de seguridad <code>apiKey</code> / <code>X-API-Token</code>) y se sirve sin autenticación:
        </p>
        <ul className="list-inside list-disc space-y-1">
          <li>
            <ExternalLink href="/api/v1/openapi.json" testId="api-reference-openapi-json">
              /api/v1/openapi.json
            </ExternalLink>{" "}
            — el contrato crudo (consumido por los SDKs).
          </li>
          <li>
            <ExternalLink href="/api/v1/docs" testId="api-reference-swagger">
              /api/v1/docs
            </ExternalLink>{" "}
            — Swagger UI interactivo para explorar y probar.
          </li>
        </ul>
        <p className="text-xs">
          Sustituye la ruta por la URL pública de tu instalación (p. ej.{" "}
          <code>https://platform.example.com/api/v1/docs</code>).
        </p>
      </SectionCard>

      <SectionCard title="Autenticación, scope y aislamiento" testId="api-reference-auth">
        <ul className="list-inside list-disc space-y-1">
          <li>
            Cabecera <code>X-API-Token: &lt;token&gt;</code> en toda request (nunca query param).
          </li>
          <li>
            <code>GET</code> requiere scope <code>read</code>; <code>POST</code> requiere{" "}
            <code>write</code>. Un <code>write</code> no concede <code>read</code> implícito.
          </li>
          <li>
            El aislamiento lo garantiza PostgreSQL RLS, no el código del endpoint: un token del
            tenant A nunca lee ni escribe filas de B (un id ajeno es un <code>404</code> limpio).
          </li>
          <li>
            Rate limit sliding-window por token (default 100 req/min) con cabeceras{" "}
            <code>X-RateLimit-*</code>; sobre presupuesto → <code>429</code>.
          </li>
          <li>
            Toda lista es paginada (<code>limit</code>/<code>offset</code> acotados); una respuesta
            nunca es ilimitada.
          </li>
        </ul>
        <CodeBlock
          lang="bash"
          code={`curl "https://platform.example.com/api/v1/projects?limit=50&offset=0" \\
  -H "X-API-Token: $TOKEN"
# La respuesta trae siempre 'X-API-Version: v1' y 'X-RateLimit-*'.`}
        />
      </SectionCard>

      <SectionCard title="Endpoints (scope mínimo)" testId="api-reference-endpoints">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-foreground border-b">
              <tr>
                <th className="py-2 pr-4 font-semibold">Endpoint</th>
                <th className="py-2 pr-4 font-semibold">Método</th>
                <th className="py-2 font-semibold">Scope</th>
              </tr>
            </thead>
            <tbody>
              {ENDPOINTS.map(([path, method, scope]) => (
                <tr key={path} className="border-b last:border-0">
                  <td className="py-1.5 pr-4">
                    <code>{path}</code>
                  </td>
                  <td className="py-1.5 pr-4">{method}</td>
                  <td className="py-1.5">
                    <code>{scope}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="Códigos de estado" testId="api-reference-status">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-foreground border-b">
              <tr>
                <th className="py-2 pr-4 font-semibold">Código</th>
                <th className="py-2 font-semibold">Significado</th>
              </tr>
            </thead>
            <tbody>
              {STATUS.map(([code, meaning]) => (
                <tr key={code} className="border-b last:border-0">
                  <td className="py-1.5 pr-4">
                    <code>{code}</code>
                  </td>
                  <td className="py-1.5">{meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
