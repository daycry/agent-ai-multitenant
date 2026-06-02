import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Small static building blocks shared by the developer-portal pages
 * (Plan 15, task_15_25). No client state, no data fetching — just
 * presentational primitives so each page stays a thin static document.
 */

export function PageIntro({
  title,
  lead,
  testId,
}: {
  title: string;
  lead: string;
  testId: string;
}) {
  return (
    <div className="mb-8" data-testid={testId}>
      <h1 className="text-foreground text-3xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground mt-2 max-w-2xl text-sm leading-relaxed">{lead}</p>
    </div>
  );
}

export function SectionCard({
  title,
  children,
  testId,
}: {
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <Card data-testid={testId}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-muted-foreground space-y-3 text-sm leading-relaxed">
        {children}
      </CardContent>
    </Card>
  );
}

/**
 * Static, copy-friendly code block. We render highlighted text as a plain
 * <pre> on purpose: no syntax-highlight runtime, no hydration, nothing to
 * break in e2e without a backend.
 */
export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  return (
    <pre
      className="bg-muted text-foreground overflow-x-auto rounded-lg border p-4 text-xs leading-relaxed"
      data-lang={lang}
    >
      <code>{code}</code>
    </pre>
  );
}

export function ExternalLink({
  href,
  children,
  testId,
}: {
  href: string;
  children: ReactNode;
  testId?: string;
}) {
  const isInternal = href.startsWith("/");
  const className = "text-primary inline-flex items-center gap-1 font-medium hover:underline";
  if (isInternal) {
    return (
      <Link href={href} className={className} data-testid={testId}>
        {children}
      </Link>
    );
  }
  return (
    <a
      href={href}
      className={className}
      data-testid={testId}
      target={href.startsWith("http") ? "_blank" : undefined}
      rel={href.startsWith("http") ? "noreferrer" : undefined}
    >
      {children}
      {href.startsWith("http") && <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />}
    </a>
  );
}
