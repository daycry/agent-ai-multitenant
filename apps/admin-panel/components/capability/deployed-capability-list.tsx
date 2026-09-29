"use client";

/**
 * Lo desplegado desde el marketplace en el proyecto, para las pestañas Tools y Skills
 * de Capacidades (`task_ui_20`). Retirar un despliegue vive en la ficha de su instalación.
 */

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { DeployedCapability } from "@/lib/capability/project-capabilities";
import { useT } from "@/lib/i18n";

export function DeployedCapabilityList({
  items,
  emptyText,
  emptyTestId,
}: {
  items: DeployedCapability[];
  emptyText: string;
  emptyTestId: string;
}) {
  const t = useT("projectCapabilities");
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-muted-foreground text-sm italic" data-testid={emptyTestId}>
            {emptyText}
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.deployment_id} data-testid={`deployed-${item.deployment_id}`}>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-2 py-4">
              <span className="font-medium">{item.name}</span>
              <Badge variant="info">{t("deployedVersion", { version: item.version })}</Badge>
              <Badge variant="muted">{t("agentsCount", { count: item.agents })}</Badge>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}
