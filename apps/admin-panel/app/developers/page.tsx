import Link from "next/link";
import type { Metadata } from "next";
import { BookOpen, Code2, FileText, Terminal, Webhook } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { PageIntro } from "./portal-ui";

export const metadata: Metadata = {
  title: "Portal de desarrollador · Agentic Platform",
  description:
    "API pública v1, SDKs Python y TypeScript, tutoriales y enlaces a la documentación canónica.",
};

interface Entry {
  href: string;
  title: string;
  body: string;
  icon: typeof Code2;
  testId: string;
}

const ENTRIES: readonly Entry[] = [
  {
    href: "/developers/api-reference",
    title: "API Reference",
    body: "El contrato OpenAPI 3.1 de /api/v1 y el Swagger UI interactivo. Autenticación por X-API-Token, scopes, rate limit y paginación.",
    icon: Code2,
    testId: "dev-portal-card-api-reference",
  },
  {
    href: "/developers/sdks",
    title: "SDKs oficiales",
    body: "Clientes tipados Python (agentic-platform-sdk) y TypeScript (@agentic-platform/sdk). Instalación + quickstart, generados desde el OpenAPI.",
    icon: Terminal,
    testId: "dev-portal-card-sdks",
  },
  {
    href: "/developers/tutorials",
    title: "Tutoriales",
    body: "Tres pasos guiados: acuñar un token, llamar a la API y configurar un webhook entrante.",
    icon: BookOpen,
    testId: "dev-portal-card-tutorials",
  },
  {
    href: "/developers/webhooks",
    title: "Webhooks entrantes",
    body: "Orígenes soportados (GitHub, GitLab, Jira, Sentry, Linear, genérico), firma HMAC-SHA256 y orden de checks fail-closed.",
    icon: Webhook,
    testId: "dev-portal-card-webhooks",
  },
];

export default function DevPortalHome() {
  return (
    <div>
      <PageIntro
        title="Portal de desarrollador"
        lead="Todo lo que necesitas para integrar con la API pública v1 de Agentic Platform: el contrato OpenAPI, los SDKs oficiales, tutoriales paso a paso y los enlaces a la documentación canónica del producto."
        testId="dev-portal-intro"
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {ENTRIES.map((entry) => (
          <Link key={entry.href} href={entry.href} data-testid={entry.testId}>
            <Card interactive className="h-full">
              <CardHeader>
                <div className="flex items-center gap-2">
                  <entry.icon className="text-primary h-5 w-5" aria-hidden="true" />
                  <CardTitle>{entry.title}</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="text-muted-foreground text-sm leading-relaxed">
                {entry.body}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      <section className="mt-10" data-testid="dev-portal-canonical-docs">
        <h2 className="text-foreground mb-3 flex items-center gap-2 text-lg font-semibold">
          <FileText className="text-primary h-5 w-5" aria-hidden="true" />
          Documentación canónica
        </h2>
        <p className="text-muted-foreground mb-4 max-w-2xl text-sm leading-relaxed">
          La documentación completa del producto vive bajo <code>/docs</code> con la estructura
          canónica de 7 carpetas. Estas son las referencias más útiles para integrar:
        </p>
        <ul className="text-muted-foreground space-y-2 text-sm">
          <li>
            <span className="text-foreground font-medium">Referencia API pública</span> —{" "}
            <code>docs/04-reference/public-api.md</code> (todos los endpoints, scopes, tunables).
          </li>
          <li>
            <span className="text-foreground font-medium">Guía de integración</span> —{" "}
            <code>docs/03-guides/api-publica-y-webhooks.md</code> (flujo completo con curl + SDKs).
          </li>
          <li>
            <span className="text-foreground font-medium">Runbooks operativos</span> —{" "}
            <code>docs/06-runbooks/</code> (instalación, troubleshooting, upgrade, DR, rotación de
            claves, capacity).
          </li>
        </ul>
      </section>
    </div>
  );
}
