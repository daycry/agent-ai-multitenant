/**
 * Honestidad de estado del subsistema de memoria (Plan 06.17 task_06_17_06).
 *
 * Estrella polar del plan (training-model.md): "ninguna capacidad parece activa
 * si no lo está; lo roto se marca 'No disponible aún'". Varias vías de RECORDAR
 * pueden estar vacías de verdad y la UI NO debe fingir que funcionan:
 *
 *   - El detector de "similares" y el slider de umbral solo tienen sentido si
 *     ALGUNA memoria tiene `embedding` (Plan 06.17 task_06_17_03/04: el back-fill
 *     puede no haber corrido, o Ollama estar caído). Sin embeddings, el endpoint
 *     `GET /memories/{id}/similar` devuelve `[]` (memories.py:376-377) y el umbral
 *     no filtra nada → "No disponible aún".
 *   - Un agente IA con `memory_scope=private` NO memoriza nada entre runs (el
 *     Memorizer hace skip silencioso `skip_private`); la ficha debe avisarlo.
 *   - `rag_knowledge_bases` / `mcp_servers` son columnas huérfanas (placeholder
 *     sin cableado real): se etiquetan como tal, no como capacidad activa.
 *   - El modo de chat `custom` no es creable end-to-end en esta versión
 *     (queda fuera de alcance del plan): "No disponible aún".
 *
 * Módulo de lógica PURA (sin React, sin DOM): es la fuente ÚNICA que consumen
 * `settings/memories`, `memories` y `agents/[id]` para decidir el estado honesto,
 * y se testea aislado (`memory-honesty.test.ts`).
 */

import { translate } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";

/**
 * Etiqueta canónica "No disponible aún" / "Not available yet" (ES + EN).
 *
 * Se deriva del diccionario (`memoryHonesty.unavailable`) en vez de repetir los
 * literales: `translate` es puro y recibe el idioma, así que este módulo sin
 * React puede usarlo igual que un componente. Los llamantes que indexaban el
 * `Record` siguen funcionando.
 */
export const UNAVAILABLE_LABEL: Record<Lang, string> = {
  es: translate("es", "memoryHonesty", "unavailable"),
  en: translate("en", "memoryHonesty", "unavailable"),
};

/**
 * Estado honesto del detector de similares / slider de umbral.
 *
 * `hasAnyEmbedding` resume si AL MENOS una memoria del conjunto visible tiene
 * embedding. Cuando es `false` el detector no puede funcionar (similitud coseno
 * sobre vectores ausentes), así que la UI lo marca "No disponible aún" y explica
 * por qué, en vez de mostrar un control que nunca devuelve resultados.
 */
export interface MemoryDetectorState {
  /** `true` solo si hay embeddings con los que el detector puede operar. */
  available: boolean;
  /** Etiqueta "No disponible aún" cuando NO está disponible; `null` si lo está. */
  label: string | null;
  /** Motivo en lenguaje llano (vacío cuando está disponible). */
  note: string;
}

export function memoryDetectorState(hasAnyEmbedding: boolean, lang: Lang): MemoryDetectorState {
  if (hasAnyEmbedding) {
    return { available: true, label: null, note: "" };
  }
  return {
    available: false,
    label: UNAVAILABLE_LABEL[lang],
    note: translate(lang, "memoryHonesty", "detectorNote"),
  };
}

/**
 * Aviso de que `memory_scope=private` ⇒ el agente IA NO memoriza entre runs.
 *
 * Devuelve el texto del aviso (bilingüe) cuando el scope es `private`, o `null`
 * para cualquier otro scope (`team_shared` / `project_shared` / `global`), que sí
 * persisten memoria visible para futuros runs.
 */
export function privateScopeMemoryWarning(memoryScope: string, lang: Lang): string | null {
  if (memoryScope !== "private") return null;
  return translate(lang, "memoryHonesty", "privateScopeWarning");
}

/**
 * Nota para campos placeholder huérfanos (`rag_knowledge_bases`, `mcp_servers`):
 * existen en el modelo pero no están cableados, así que se etiquetan como
 * "placeholder" para no prometer una capacidad inexistente.
 */
export function placeholderFieldNote(lang: Lang): string {
  return translate(lang, "memoryHonesty", "placeholderField");
}

/**
 * Estado del modo de chat `custom`: no creable end-to-end en esta versión
 * (fuera de alcance del plan) → "No disponible aún".
 */
export function customChatModeUnavailable(lang: Lang): string {
  return UNAVAILABLE_LABEL[lang];
}
