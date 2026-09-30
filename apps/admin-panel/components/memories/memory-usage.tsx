"use client";

/**
 * Contadores de uso y de utilidad de una memoria (`task_mem_20`): cuántas veces la recuperó un
 * run (0 = «nunca»), cuándo fue la última y los votos de utilidad. Lo que falta se dice, no se
 * rellena.
 */

import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

import { type MemoryResponse } from "./types";

export function MemoryUsage({ memory }: { memory: MemoryResponse }) {
  const t = useT("memoriesCard");
  const lang = useLangOptional();
  const recalls = memory.recall_count;
  const last = memory.last_recalled_at
    ? new Date(memory.last_recalled_at).toLocaleString(lang === "en" ? "en-GB" : "es-ES")
    : null;

  return (
    <dl className="text-muted-foreground grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-2">
      <Row label={t("usageRecalls")} testid="memory-usage-recalls">
        {recalls === null || recalls === undefined || recalls === 0 ? t("usageNever") : recalls}
      </Row>
      {last ? (
        <Row label={t("usageLast")} testid="memory-usage-last">
          {last}
        </Row>
      ) : null}
      <Row label={t("usageUseful")} testid="memory-usage-useful">
        {memory.useful_count ?? 0}
      </Row>
      <Row label={t("usageHarmful")} testid="memory-usage-harmful">
        {memory.harmful_count ?? 0}
      </Row>
    </dl>
  );
}

function Row({
  label,
  testid,
  children,
}: {
  label: string;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-1">
      <dt className="font-medium">{label}:</dt>
      <dd data-testid={testid}>{children}</dd>
    </div>
  );
}
