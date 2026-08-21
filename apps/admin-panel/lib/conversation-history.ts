/**
 * Pure helpers for the project chat's conversation history (feature: start a new
 * conversation without deleting the previous ones, switch between them, delete one).
 *
 * Kept out of the page component so the non-trivial bits (which conversation
 * becomes active after a delete, how to label an untitled conversation) are unit
 * tested without rendering React.
 */

import { translate, type Lang } from "@/lib/i18n";

export interface ConversationLike {
  id: string;
  title: string | null;
  current_mode: string;
  created_at: string;
}

/**
 * After removing `deletedId` from the list, which conversation should become the
 * active one? The list is created_at-ascending (the backend orders it that way),
 * so the "most recent remaining" is the last element. Returns null when nothing
 * is left (the page then falls back to its empty state).
 */
export function nextActiveAfterDelete<T extends { id: string }>(
  conversations: T[],
  deletedId: string,
): string | null {
  const remaining = conversations.filter((c) => c.id !== deletedId);
  return remaining.length > 0 ? remaining[remaining.length - 1].id : null;
}

/**
 * Human label for a conversation in the history selector. Prefers the title;
 * falls back to a date-stamped label so untitled conversations stay
 * distinguishable instead of all reading "Conversación".
 *
 * `lang` es OBLIGATORIO y no tiene default (prod-16 `task_prod16_03`): un
 * default silencioso deja al llamante que se olvide pintando castellano en el
 * panel en inglés, que es exactamente el fallo que este plan cierra. Al no ser
 * un componente, resuelve con `translate()` en vez de con `useT()`.
 */
export function conversationLabel(c: ConversationLike, lang: Lang): string {
  if (c.title && c.title.trim()) return c.title.trim();
  const d = new Date(c.created_at);
  if (Number.isNaN(d.getTime())) return translate(lang, "projectChat", "untitledConversation");
  const stamp = `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
  return translate(lang, "projectChat", "untitledConversationAt", { stamp });
}
