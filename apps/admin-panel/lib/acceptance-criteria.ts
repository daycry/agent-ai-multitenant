/**
 * Pure helpers for a task's `acceptance_criteria` (the agent/reviewer's
 * "definition of done"). A criterion is either a plain descriptive **string**
 * (the planner / manual-edit shape — see ADR Feature A) or a structured
 * **object** (e.g. a reviewer criterion carrying `id`/`kind`/metadata). The
 * agent renders both via the same `_criterion_text` rule server-side.
 *
 * The editor in `task-detail-sheet.tsx` keeps logic out of the component: it
 * seeds each editable row from `criterionText(original)` and, on save, rebuilds
 * the list with `cleanCriteria` — preserving structured criteria instead of
 * flattening them to strings.
 */

/** Mirror of the planner's caps (`planning_llm._MAX_ACCEPTANCE_CRITERIA`). */
export const MAX_ACCEPTANCE_CRITERIA = 8;
/** Mirror of the planner's per-criterion length cap (`_MAX_CRITERION_LEN`). */
export const MAX_CRITERION_LEN = 300;

/** A single editable row: the shown/edited text plus the original criterion it
 * came from (`null` for a row the operator added from scratch). */
export interface CriterionDraft {
  text: string;
  original: unknown;
}

/** Flatten any criterion shape to a display string. Strings pass through; objects
 * use the first of description/text/criterion/name; anything else → JSON. */
export function criterionText(c: unknown): string {
  if (typeof c === "string") return c;
  if (c && typeof c === "object") {
    const o = c as Record<string, unknown>;
    return String(o.description ?? o.text ?? o.criterion ?? o.name ?? JSON.stringify(c));
  }
  return String(c);
}

/** True when `c` is a structured criterion (a plain object we should preserve),
 * not a string and not an array. */
function isStructured(c: unknown): c is Record<string, unknown> {
  return typeof c === "object" && c !== null && !Array.isArray(c);
}

/**
 * Rebuild a clean `acceptance_criteria` list from editor rows: trim each text,
 * drop empties, cap length and count. A row backed by a structured criterion is
 * preserved (only its `description` is overwritten) so reviewer/QA metadata is
 * never lost; string-backed and brand-new rows emit plain strings.
 */
export function cleanCriteria(drafts: readonly CriterionDraft[]): unknown[] {
  const out: unknown[] = [];
  for (const draft of drafts) {
    const text = draft.text.trim().slice(0, MAX_CRITERION_LEN).trim();
    if (!text) continue;
    if (isStructured(draft.original)) {
      out.push({ ...draft.original, description: text });
    } else {
      out.push(text);
    }
    if (out.length >= MAX_ACCEPTANCE_CRITERIA) break;
  }
  return out;
}
