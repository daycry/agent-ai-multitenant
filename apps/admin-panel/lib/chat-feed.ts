/**
 * Chat feed helpers — when is the team still replying, so the UI keeps polling
 * (and shows "thinking") until the whole turn has landed.
 *
 * Why a time window: a PLANNING turn is not a single reply — the PM convenes
 * specialists and emits MANY messages (PM framing → each specialist → synthesis)
 * over several minutes, each step up to ~150s apart (the backend per-step
 * timeout). The old "poll only while the last message is the user's" logic
 * stopped the moment the PM framing arrived, so the specialists + synthesis only
 * showed up on a manual reload. The WebSocket pushes each step live, but if it
 * dropped (proxy idle-timeout, sleep/wake) this poll is the safety net — it must
 * stay alive for the whole turn.
 */

/** The fields the feed helpers need from a chat message. */
export interface FeedMessage {
  author_kind: "user" | "agent" | "system";
  mode: string;
  created_at: string;
  attachments?: Array<Record<string, unknown>>;
}

/** Safety-net poll cadence (ms) while a reply/turn is still in flight. */
export const CHAT_POLL_MS = 3000;

/**
 * Keep the planning turn "in flight" while its last message is younger than
 * this. Comfortably above the backend per-step timeout (150s) so a slow
 * specialist step never makes the poll give up mid-turn.
 */
export const PLANNING_INFLIGHT_WINDOW_MS = 180_000;

/**
 * Is the team still replying to the latest user turn?
 *
 * - Last message is the user's → awaiting the first reply.
 * - Planning turns emit many messages; treat the turn as in flight while the
 *   last planning message is recent AND has not yet presented a plan (the
 *   synthesis carries an attachment — a clear "turn done" signal).
 * - Other modes produce a single reply, so they settle as soon as it arrives.
 */
export function isReplyInFlight(
  messages: readonly FeedMessage[] | undefined,
  nowMs: number,
): boolean {
  if (!messages || messages.length === 0) return false;
  const last = messages[messages.length - 1];
  if (last.author_kind === "user") return true;
  if (last.mode === "planning") {
    const presentedPlan = Array.isArray(last.attachments) && last.attachments.length > 0;
    if (presentedPlan) return false;
    const ageMs = nowMs - Date.parse(last.created_at);
    // Negative age (clock skew → message "from the future") means very fresh, so
    // treat it as in flight; only a finite age past the window settles the turn.
    return Number.isFinite(ageMs) && ageMs < PLANNING_INFLIGHT_WINDOW_MS;
  }
  return false;
}

/** React Query `refetchInterval` value derived from {@link isReplyInFlight}. */
export function chatRefetchInterval(
  messages: readonly FeedMessage[] | undefined,
  nowMs: number,
): number | false {
  return isReplyInFlight(messages, nowMs) ? CHAT_POLL_MS : false;
}
