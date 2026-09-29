"use client";

/**
 * Catálogo de tools navegable — `/admin/tools` (Plan 06.18 task_06_18_11).
 *
 * Resuelve dos quejas concretas del operador:
 *   - "no existe un catálogo": antes solo se veían las tools desde la ficha
 *     de cada agente, sin un sitio donde explorarlas/gestionarlas.
 *   - "tools duplicadas": el alta de custom valida contra el backend
 *     (`POST /tools` devuelve 409 si el nombre colisiona con una built-in o
 *     con otra tool del tenant — task_06_18_04), así que no aparecen dos
 *     filas idénticas.
 *
 * Faceted browse por las TRES facetas de ADR 0049 (Función / Seguridad /
 * Origen) usando los value-sets + resolvers de `@/lib/tools/taxonomy`
 * (fuente única de labels/variants, task_06_18_10) + búsqueda libre. Las
 * built-in son de SOLO LECTURA (badge + sin acciones de editar/borrar); las
 * custom del tenant se crean/editan/borran (admin only, vía <RoleGuard>).
 *
 * Una tool sin motor en el runtime (`is_runtime_wired === false`, ADR 0049)
 * se marca "No disponible aún" para no engañar al operador.
 *
 * Troceada en piezas colocadas (prod-16 `task_prod16_08`): `tool-types.ts`,
 * `tool-facet-select.tsx`, `tool-catalog-rows.tsx` y `tool-dialogs.tsx`.
 * Aquí queda el estado de la pantalla —filtros, búsqueda y qué diálogo está
 * abierto— y nada más.
 *
 * `task_ui_30`: la pantalla es «Skills y tools» - dos pestañas, Tools (por
 * defecto; el catálogo de arriba vive en `tools-tab.tsx`) y Skills
 * (`skills-tab.tsx`). `?tab=skills` abre Skills.
 */

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

import { SkillsTab } from "./skills-tab";
import { ToolsTab } from "./tools-tab";

type PageTab = "tools" | "skills";

function isTab(v: string | null | undefined): v is PageTab {
  return v === "tools" || v === "skills";
}

function ToolsAndSkills() {
  const t = useT("tools");
  const requested = useSearchParams()?.get("tab");
  const [picked, setPicked] = useState<PageTab | null>(null);
  const active: PageTab = picked ?? (isTab(requested) ? requested : "tools");

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="tools-catalog-page"
    >
      <Tabs value={active} defaultValue={active} onValueChange={(v) => isTab(v) && setPicked(v)}>
        <TabsList aria-label={t("pageTabsAria")} data-testid="tools-page-tabs">
          <TabsTrigger value="tools" data-testid="tools-page-tab-tools">
            {t("tabTools")}
          </TabsTrigger>
          <TabsTrigger value="skills" data-testid="tools-page-tab-skills">
            {t("tabSkills")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="tools">
          <ToolsTab />
        </TabsContent>
        <TabsContent value="skills">
          <SkillsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// `useSearchParams` necesita un límite de Suspense en el App Router.
export default function ToolsCatalogPage() {
  return (
    <Suspense fallback={null}>
      <ToolsAndSkills />
    </Suspense>
  );
}
