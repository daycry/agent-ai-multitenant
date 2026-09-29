"use client";

/**
 * Ficha de procedencia de una memoria (task_ui_31): quién la dejó, cuándo,
 * dónde vive y cuántas veces se recuperó.
 *
 * Honestidad: lo que el dominio no guardó se enseña como «desconocido», nunca
 * se rellena a ojo. `recall_count` llega `null` hasta que `task_mem_02` cuente
 * las recuperaciones, y las memorias antiguas no tienen agente, ejecución ni
 * sesión: ahí «quién» es desconocido.
 */

import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

import { type MemoriesProvenanceKey, type MemoryResponse } from "./types";

const KIND_KEY = {
  agent: "byAgent",
  human: "byHuman",
  user: "byUser",
  unknown: "unknown",
} as const satisfies Record<string, MemoriesProvenanceKey>;

export function MemoryProvenance({ memory }: { memory: MemoryResponse }) {
  const t = useT("memoriesProvenance");
  const lang = useLangOptional();
  const by = memory.created_by ?? { kind: "unknown" as const, id: null, name: null };
  const who =
    by.kind === "unknown" ? t("unknown") : `${by.name ?? t("unknown")} · ${t(KIND_KEY[by.kind])}`;

  const where: string[] = [];
  if (memory.scope === "global") where.push(t("whereGlobal"));
  if (memory.scope === "private") where.push(t("wherePrivate"));
  if (memory.project_id) where.push(`${t("project")}: ${memory.project_name ?? t("unknown")}`);
  if (memory.team_id) where.push(`${t("team")}: ${memory.team_name ?? t("unknown")}`);
  if (memory.agent_id) where.push(`${t("agent")}: ${memory.agent_name ?? t("unknown")}`);

  const executionId = memory.execution_id ?? memory.source_execution_id;
  const when = new Date(memory.created_at).toLocaleString(lang === "en" ? "en-GB" : "es-ES");
  const recall =
    memory.recall_count === null || memory.recall_count === undefined
      ? t("unknown")
      : t("recallTimes", { count: memory.recall_count });

  return (
    <dl
      className="text-muted-foreground mt-2 grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-2"
      data-testid={`memory-provenance-${memory.id}`}
    >
      <Field label={t("who")} value={who} testid={`memory-who-${memory.id}`} />
      <Field label={t("when")} value={when} testid={`memory-when-${memory.id}`} />
      <Field
        label={t("where")}
        value={where.join(" · ") || t("unknown")}
        testid={`memory-where-${memory.id}`}
      />
      <Field label={t("recalled")} value={recall} testid={`memory-recall-${memory.id}`} />
      {executionId ? (
        <Field
          label={t("execution")}
          value={executionId.slice(0, 8)}
          testid={`memory-execution-${memory.id}`}
        />
      ) : null}
    </dl>
  );
}

function Field({ label, value, testid }: { label: string; value: string; testid: string }) {
  return (
    <div className="flex gap-1">
      <dt className="font-medium">{label}:</dt>
      <dd data-testid={testid}>{value}</dd>
    </div>
  );
}
