"use client";

import { ResourceDetailPanel } from "@/components/resource";
import { useT } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";
import {
  label as taxonomyLabel,
  resolveCategory,
  resolveImpl,
  resolveSecurity,
} from "@/lib/tools/taxonomy";

import type { CatalogTool } from "./tool-types";

/** Resumen lateral de una tool: sus tres facetas y su procedencia. */
export function ToolPanel({
  tool,
  lang,
  onClose,
}: {
  tool: CatalogTool;
  lang: Lang;
  onClose: () => void;
}) {
  const t = useT("tools");
  const tr = useT("resource");
  return (
    <ResourceDetailPanel
      title={tool.name}
      onClose={onClose}
      fields={[
        {
          key: "provenance",
          label: tr("fieldProvenance"),
          value: tool.is_builtin ? tr("provBuiltin") : tr("provOwn"),
        },
        ...(tool.description
          ? [{ key: "description", label: tr("fieldDescription"), value: tool.description }]
          : []),
        {
          key: "category",
          label: t("facetCategory"),
          value: taxonomyLabel(resolveCategory(tool.category, lang), lang),
        },
        {
          key: "security",
          label: t("facetSecurity"),
          value: taxonomyLabel(resolveSecurity(tool.security_level, lang), lang),
        },
        {
          key: "impl",
          label: t("facetImpl"),
          value: taxonomyLabel(resolveImpl(tool.implementation_type, lang), lang),
        },
        ...(tool.is_runtime_wired
          ? []
          : [{ key: "unwired", label: t("unwiredBadge"), value: t("unwiredTooltip") }]),
      ]}
    />
  );
}
