import type { Metadata } from "next";

import { CodeBlock, ExternalLink, PageIntro, SectionCard } from "../portal-ui";

export const metadata: Metadata = {
  title: "Tutoriales · Portal de desarrollador",
  description:
    "Tutoriales paso a paso: acuñar un X-API-Token, llamar a la API v1 y configurar un webhook entrante.",
};

const MINT = `# Autenticado como Tenant Admin (cookie de sesión / Bearer de la UI).
curl -X POST https://platform.example.com/auth/api-tokens \\
  -H "Content-Type: application/json" \\
  -b "$SESSION_COOKIE" \\
  -d '{
    "name": "ci-readonly",
    "scopes": ["read"],
    "rate_limit": 200,
    "expires_at": "2027-01-01T00:00:00Z",
    "ip_allowlist": ["203.0.113.10"]
  }'
# → 201 { "id": "...", "prefix": "tkn_abc", "token": "tkn_abc...<solo aquí>", ... }`;

const CALL = `export TOKEN="tkn_abc..."

# Listar proyectos del propio tenant (paginado)
curl "https://platform.example.com/api/v1/projects?limit=50&offset=0" \\
  -H "X-API-Token: $TOKEN"

# Crear un proyecto (requiere scope write)
curl -X POST https://platform.example.com/api/v1/projects \\
  -H "X-API-Token: $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Mi proyecto"}'`;

const WEBHOOK = `# Crear la config (devuelve el secreto de firma UNA vez)
curl -X POST https://platform.example.com/projects/$PROJECT_ID/incoming-webhooks \\
  -H "Content-Type: application/json" \\
  -b "$SESSION_COOKIE" \\
  -d '{
    "origin": "github",
    "name": "GitHub → tareas de revisión",
    "enabled": true,
    "action_mappings": [
      {"event_type": "github.pull_request_review",
       "action": "create_task",
       "title_template": "Review: {title}",
       "body_template": "{body}\\n\\nde {actor}"}
    ]
  }'
# → 201 { "id": "<config_id>",
#         "incoming_path": "/webhooks/incoming/github/<config_id>",
#         "signing_secret": "<solo aquí — cópialo al proveedor>" }`;

export default function TutorialsPage() {
  return (
    <div className="space-y-6">
      <PageIntro
        title="Tutoriales"
        lead="Tres pasos para pasar de cero a integrado. Requieren el stack arriba y el rol Tenant Admin (acuñar tokens y gestionar webhooks lo exigen). Sustituye platform.example.com por la URL pública de tu instalación."
        testId="tutorials-intro"
      />

      <SectionCard title="1 · Acuñar un X-API-Token" testId="tutorials-mint">
        <p>
          El token es la credencial por tenant del API público. Lo acuña el Tenant Admin en{" "}
          <code>/auth/api-tokens</code>. El token claro se devuelve exactamente una vez — guárdalo,
          no se puede recuperar.
        </p>
        <CodeBlock lang="bash" code={MINT} />
        <ul className="list-inside list-disc space-y-1 text-xs">
          <li>
            <code>scopes</code>: <code>[&quot;read&quot;]</code> solo permite GET; añade{" "}
            <code>&quot;write&quot;</code> para crear recursos.
          </li>
          <li>
            <code>rate_limit</code> / <code>expires_at</code> / <code>ip_allowlist</code> son
            opcionales.
          </li>
          <li>
            Revoca con <code>DELETE /auth/api-tokens/&#123;token_id&#125;</code> (efectivo de
            inmediato).
          </li>
        </ul>
      </SectionCard>

      <SectionCard title="2 · Llamar a la API v1" testId="tutorials-call">
        <p>
          El token viaja siempre en la cabecera <code>X-API-Token</code>. Los GET piden scope{" "}
          <code>read</code>, los POST <code>write</code>. Un id de otro tenant es un{" "}
          <code>404</code>; sobre el rate limit es un <code>429</code>.
        </p>
        <CodeBlock lang="bash" code={CALL} />
        <p className="text-xs">
          ¿Prefieres un cliente tipado? Mira la página de{" "}
          <ExternalLink href="/developers/sdks" testId="tutorials-link-sdks">
            SDKs
          </ExternalLink>{" "}
          (Python y TypeScript).
        </p>
      </SectionCard>

      <SectionCard title="3 · Configurar un webhook entrante" testId="tutorials-webhook">
        <p>
          Un webhook entrante deja que un tool externo (GitHub, GitLab, Jira, Sentry, Linear,
          genérico) empuje un evento que se convierte en una acción del sistema (crear tarea /
          comentar tarea / escalar).
        </p>
        <CodeBlock lang="bash" code={WEBHOOK} />
        <p className="text-xs">
          El secreto de firma se devuelve en claro una sola vez; la URL pública lleva el{" "}
          <code>config_id</code>, nunca el secreto. Para la verificación HMAC y el orden de checks
          completo, ver la página de{" "}
          <ExternalLink href="/developers/webhooks" testId="tutorials-link-webhooks">
            Webhooks
          </ExternalLink>
          .
        </p>
      </SectionCard>
    </div>
  );
}
