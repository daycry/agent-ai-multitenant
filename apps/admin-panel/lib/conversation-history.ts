/**
 * Pure helpers for the project chat's conversation history (feature: start a new
 * conversation without deleting the previous ones, switch between them, delete one).
 *
 * Kept out of the page component so the non-trivial bits (which conversation
 * becomes active after a delete, how to label an untitled conversation) are unit
 * tested without rendering React.
 */

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
 */
export function conversationLabel(c: ConversationLike): string {
  if (c.title && c.title.trim()) return c.title.trim();
  const d = new Date(c.created_at);
  if (Number.isNaN(d.getTime())) return "Conversación sin título";
  return `Conversación · ${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}
