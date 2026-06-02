import Link from "next/link";
import type { ReactNode } from "react";
import { BookOpen, Code2, Sparkles, Terminal, Webhook } from "lucide-react";

/**
 * Public developer-portal shell (Plan 15, task_15_25).
 *
 * Lightweight + static-on-purpose: this route group is a plain set of
 * server-rendered React pages under the admin-panel app. It is NOT inside
 * the auth-gated `/admin` segment, so the portal is reachable without a
 * session (a developer reads the contract before they have a token) and
 * makes **no** API calls — which keeps its e2e green without a live stack.
 *
 * We deliberately did not add a heavyweight docs framework (Docusaurus,
 * Nextra, Mintlify…): the API reference is already served by FastAPI as
 * Swagger UI at `/api/v1/docs`, the SDKs ship their own READMEs, and the
 * canonical product docs live under `/docs`. The portal is the thin
 * public landing that surfaces and links those existing sources.
 */

interface NavItem {
  href: string;
  label: string;
  icon: typeof Code2;
}

const NAV: readonly NavItem[] = [
  { href: "/developers", label: "Inicio", icon: Sparkles },
  { href: "/developers/api-reference", label: "API Reference", icon: Code2 },
  { href: "/developers/sdks", label: "SDKs", icon: Terminal },
  { href: "/developers/tutorials", label: "Tutoriales", icon: BookOpen },
  { href: "/developers/webhooks", label: "Webhooks", icon: Webhook },
];

export default function DevPortalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="bg-background min-h-screen" data-testid="dev-portal">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link
            href="/developers"
            className="flex items-center gap-2"
            data-testid="dev-portal-brand"
          >
            <span
              className="bg-brand-gradient inline-flex h-9 w-9 items-center justify-center rounded-xl"
              aria-hidden="true"
            >
              <Sparkles className="h-5 w-5 text-white" />
            </span>
            <span className="text-foreground text-base font-semibold tracking-tight">
              Portal de desarrollador
            </span>
          </Link>
          <nav className="hidden gap-1 md:flex" aria-label="Portal de desarrollador">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                data-testid={`dev-portal-nav-${item.label.toLowerCase()}`}
                className="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
              >
                <item.icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      <footer className="text-muted-foreground border-t py-6 text-center text-xs">
        Agentic Platform · API pública v1 · documentación pública
      </footer>
    </div>
  );
}
