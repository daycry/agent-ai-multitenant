import type { Metadata } from "next";

import { CodeBlock, PageIntro, SectionCard } from "../portal-ui";

export const metadata: Metadata = {
  title: "Webhooks · Portal de desarrollador",
  description:
    "Webhooks entrantes: orígenes soportados, firma HMAC-SHA256 y orden de checks fail-closed.",
};

const ORIGINS: ReadonlyArray<[string, string, string]> = [
  ["github", "X-Hub-Signature-256: sha256=<hex>", "push, PR review → crear/actualizar tarea"],
  ["gitlab", "X-Hub-Signature-256: sha256=<hex>", "merge request → crear tarea"],
  ["jira", "X-Signature-256: <hex> (bare)", "issue creado → crear tarea"],
  ["sentry", "X-Signature-256: <hex> (bare)", "error → crear bug task / escalar"],
  ["linear", "X-Signature-256: <hex> (bare)", "issue → crear tarea"],
  ["generic", "X-Signature-256: <hex> (bare)", "integración a medida / proxy normalizador"],
];

const CHECKS: ReadonlyArray<[string, string]> = [
  ["1 · Body cap (413)", "Antes de leer el body (guarda anti-DDoS; default 1 MiB)."],
  [
    "2 · Resolver config (404)",
    "config_id → fila. Inexistente / soft-deleted / deshabilitada / con origin que no casa → 404 (nunca revela si un id existe).",
  ],
  [
    "3 · Rate limit por config (429)",
    "Sliding-window keyed por config_id (default 120/min); una config nunca throttlea a otra.",
  ],
  [
    "4 · Verificar HMAC (401, SIN acción)",
    "Recomputa el MAC con el secreto por proyecto y compara en tiempo constante. Firma mala/ausente/manipulada → 401, nada persistido.",
  ],
  [
    "5 · Mapear + actuar",
    "Normaliza el payload (plantilla del origen) y resuelve action_mappings → acción (crear tarea / comentar / escalar), en la misma transacción que registra el evento.",
  ],
  [
    "6 · Persistir (idempotente)",
    "UNIQUE parcial (config_id, delivery_id): una redelivery colisiona, así que ni el evento ni su acción se reaplican.",
  ],
];

const VERIFY = `BODY='{"hello":"world"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST https://platform.example.com/webhooks/incoming/generic/$CONFIG_ID \\
  -H "Content-Type: application/json" \\
  -H "X-Signature-256: $SIG" \\
  -d "$BODY"
# → 202 { "status": "accepted", "event_id": "...", "action": "create_task", "task_id": "..." }`;

export default function WebhooksPage() {
  return (
    <div className="space-y-6">
      <PageIntro
        title="Webhooks entrantes"
        lead="La dirección inversa del firmado saliente: un tool externo hace POST de un evento firmado con HMAC y el sistema lo verifica y lo mapea a una acción. El endpoint es público — la HMAC ES la autenticación."
        testId="webhooks-intro"
      />

      <SectionCard title="Orígenes soportados (catálogo cerrado)" testId="webhooks-origins">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-foreground border-b">
              <tr>
                <th className="py-2 pr-4 font-semibold">origin</th>
                <th className="py-2 pr-4 font-semibold">Cabecera de firma</th>
                <th className="py-2 font-semibold">Eventos típicos</th>
              </tr>
            </thead>
            <tbody>
              {ORIGINS.map(([origin, header, events]) => (
                <tr key={origin} className="border-b last:border-0">
                  <td className="py-1.5 pr-4">
                    <code>{origin}</code>
                  </td>
                  <td className="py-1.5 pr-4">
                    <code>{header}</code>
                  </td>
                  <td className="py-1.5">{events}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs">
          Todas las firmas son HMAC-SHA256 sobre el body crudo, comparadas en tiempo constante. El
          endpoint público es{" "}
          <code>POST /webhooks/incoming/&#123;origin&#125;/&#123;config_id&#125;</code>.
        </p>
      </SectionCard>

      <SectionCard title="Orden de checks (fail-closed)" testId="webhooks-checks">
        <ol className="space-y-2">
          {CHECKS.map(([step, desc]) => (
            <li key={step}>
              <span className="text-foreground font-medium">{step}</span> — {desc}
            </li>
          ))}
        </ol>
      </SectionCard>

      <SectionCard title="Verificar la firma manualmente (genérico)" testId="webhooks-verify">
        <CodeBlock lang="bash" code={VERIFY} />
        <p className="text-xs">
          Rotación: si sospechas que el secreto se filtró,{" "}
          <code>POST …/incoming-webhooks/&#123;config_id&#125;/rotate-secret</code> devuelve un
          nuevo claro una vez; el anterior deja de verificar de inmediato.
        </p>
      </SectionCard>
    </div>
  );
}
