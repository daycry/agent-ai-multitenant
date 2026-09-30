/**
 * Tipos y mapas de etiquetas de la pantalla de memorias (`/admin/memories`).
 * Los mapas siguen siendo `Record<enum, clave>` para que TypeScript exija una
 * entrada por cada valor del enum del backend.
 */
import { type BadgeVariant } from "@/components/ui/badge";
import { type Translator } from "@/lib/i18n";

export type MemoryScope = "private" | "team_shared" | "project_shared" | "global";
export type MemoryType = "episodic" | "semantic";

/** Las claves del namespace `memories`, para tipar los mapas de etiquetas. */
export type MemoriesKey = Parameters<Translator<"memories">>[0];

export interface MemoryResponse {
  id: string;
  tenant_id: string;
  scope: MemoryScope;
  type: MemoryType;
  content: string;
  tags: string[];
  user_id: string | null;
  team_id: string | null;
  project_id: string | null;
  source_execution_id: string | null;
  agent_id: string | null;
  /** Procedencia (task_ui_31). Opcionales: un backend anterior no los manda. */
  category?: MemoryType;
  execution_id?: string | null;
  human_work_session_id?: string | null;
  project_name?: string | null;
  team_name?: string | null;
  agent_name?: string | null;
  created_by?: MemoryCreatedBy;
  /** Runs que la recuperaron (`task_mem_02`); `null` sólo si falta el dato: «desconocido». */
  recall_count?: number | null;
  has_embedding: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * Clave del diccionario por scope, no el texto ya resuelto.
 *
 * El mapa sigue existiendo (en vez de disolverse en un `t("scope" + x)`)
 * porque es lo que hace que TypeScript siga exigiendo una entrada por cada
 * valor del enum: con la concatenacion, anadir un scope en el backend
 * compilaria y pintaria la clave cruda en pantalla.
 */
export const SCOPE_KEY: Record<MemoryScope | "all", MemoriesKey> = {
  all: "scopeAll",
  private: "scopePrivate",
  team_shared: "scopeTeam",
  project_shared: "scopeProject",
  global: "scopeGlobal",
};

// Colores por scope (de menos a más alcance):
//   private → muted (uso individual)
//   team_shared → info (azul, compartido con team)
//   project_shared → primary (color de marca, contexto del proyecto)
//   global → warning (naranja, "tocas esto y afecta a TODOS")
export const SCOPE_VARIANT: Record<MemoryScope, BadgeVariant> = {
  private: "muted",
  team_shared: "info",
  project_shared: "primary",
  global: "warning",
};

export const TYPE_KEY: Record<MemoryType, MemoriesKey> = {
  episodic: "typeEpisodic",
  semantic: "typeSemantic",
};

// Episódica = hecho concreto del pasado · Semántica = conocimiento durable.
export const TYPE_VARIANT: Record<MemoryType, BadgeVariant> = {
  episodic: "default",
  semantic: "info",
};

export interface MemoryCreatedBy {
  kind: "agent" | "human" | "user" | "unknown";
  id: string | null;
  name: string | null;
}

/** Contadores por pestaña (`GET /memories/counts`): sólo lo visible para quien pregunta. */
export type MemoryCounts = Record<MemoryScope | "all", number>;

export interface MemorySearchHit {
  memory: MemoryResponse;
  score: number;
}

/** Las claves del namespace `memoriesProvenance`. */
export type MemoriesProvenanceKey = Parameters<Translator<"memoriesProvenance">>[0];
