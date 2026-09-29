// Tipos y agregación pura de las bases de conocimiento (`task_ui_21`). Sin JSX ni
// hooks: la tarjeta y el asistente la comparten y el test la afirma sin montar nada.

// `indexed_empty` es terminal (0 chunks: el documento no tenía texto) — CHECK de la
// migración 0080 y `ingestion/pipeline.py`. No es un fallo ni un éxito con contenido.
export type DocumentStatus = "pending" | "processing" | "indexed" | "indexed_empty" | "failed";

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
  /** Agregados calculados en SQL por el listado (no por la primera página de documentos). */
  document_stats?: KbDocumentStats | null;
  granted_projects?: KbGrantedProject[] | null;
}

export interface KbDocumentStats {
  total: number;
  by_status: Record<string, number>;
  last_updated_at: string | null;
}

export interface KbGrantedProject {
  project_id: string;
  name: string;
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
  return status === "indexed" || status === "indexed_empty" || status === "failed";
}

/**
 * Estado agregado de una base a partir de los agregados del listado: «con
 * fallos» gana a «indexando», y ésa a «indexada» — un solo documento fallido no
 * debe esconderse tras un verde. `indexed_empty` cuenta como indexado.
 */
export function summarizeStats(stats: KbDocumentStats | null | undefined): DocumentsSummary {
  const total = stats?.total ?? 0;
  const by = stats?.by_status ?? {};
  const failed = by.failed ?? 0;
  const indexed = (by.indexed ?? 0) + (by.indexed_empty ?? 0);
  const inProgress = Math.max(0, total - failed - indexed);
  let state: IndexingState = "indexed";
  if (total === 0) state = "empty";
  else if (failed > 0) state = "failed";
  else if (inProgress > 0) state = "indexing";
  return {
    count: total,
    indexed,
    failed,
    inProgress,
    state,
    lastUpdate: stats?.last_updated_at ?? null,
  };
}
