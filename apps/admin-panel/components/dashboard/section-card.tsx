import type React from "react";

import { Card, CardContent, CardTitle } from "@/components/ui/card";

/** La tarjeta de una sección del dashboard: título, contenido y, si lo hay, un enlace. */
export function SectionCard({
  title,
  testid,
  action,
  children,
}: {
  title: string;
  testid: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex h-full flex-col" data-testid={testid}>
      <CardContent className="flex flex-1 flex-col gap-3 p-5">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{title}</CardTitle>
          {action}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

/** El texto de una sección vacía. */
export function SectionEmpty({ testid, children }: { testid: string; children: React.ReactNode }) {
  return (
    <p className="text-muted-foreground text-sm" data-testid={testid}>
      {children}
    </p>
  );
}
