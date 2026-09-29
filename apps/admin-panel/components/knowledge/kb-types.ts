// Tipos y agregación pura de las bases de conocimiento (`task_ui_21`). Sin JSX ni
// hooks: la tarjeta y el asistente la comparten y el test la afirma sin montar nada.

export type DocumentStatus = "pending" | "processing" | "indexed" | "failed";

export interface KbDocument {
  id: string;
  kb_id: string;
  title: string;
  source_filename: string;
  source_mime_type: string;
  source_size_bytes: number;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number;
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Lo mínimo que el asistente y la tarjeta necesitan de una base. */
export interface KbRef {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  embedding_model_stale: boolean;
  category: { id: string; name: string } | null;
}

export type IndexingState = "empty" | "indexed" | "indexing" | "failed";

export interface DocumentsSummary {
  count: number;
  indexed: number;
  failed: number;
  inProgress: number;
  state: IndexingState;
  /** El `updated_at` más reciente de sus documentos, o null si no hay ninguno. */
  lastUpdate: string | null;
}

export function isTerminal(status: DocumentStatus): boolean {
  return status === "indexed" || status === "failed";
}

/**
 * Estado agregado de una base: «con fallos» gana a «indexando», y ésa a
 * «indexada» — un solo documento fallido no debe esconderse tras un verde.
 */
export function summarizeDocuments(docs: KbDocument[]): DocumentsSummary {
  const failed = docs.filter((d) => d.status === "failed").length;
  const indexed = docs.filter((d) => d.status === "indexed").length;
  const inProgress = docs.length - failed - indexed;
  let state: IndexingState = "indexed";
  if (docs.length === 0) state = "empty";
  else if (failed > 0) state = "failed";
  else if (inProgress > 0) state = "indexing";
  const lastUpdate = docs.reduce<string | null>(
    (best, d) => (best === null || d.updated_at > best ? d.updated_at : best),
    null,
  );
  return { count: docs.length, indexed, failed, inProgress, state, lastUpdate };
}
