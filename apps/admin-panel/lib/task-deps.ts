/**
 * Pure dependency-state derivation for Kanban task cards.
 *
 * The board already loads every task of the selected plan (with its status)
 * plus each task's `depends_on` ids, so we can decide "is this card blocked by
 * an unfinished dependency?" entirely client-side — no extra request. The
 * server (chat/dag_enforcement) enforces the same rule authoritatively on the
 * PUT that moves a task to `ready`; this is only the visual + UX guard.
 */

export interface DepState {
  /** The task declares at least one dependency. */
  hasDeps: boolean;
  /** At least one dependency is not yet `done`. */
  blocked: boolean;
  /** How many dependencies are still not `done`. */
  pendingCount: number;
}

const DONE = "done";

/**
 * Derive the dependency state of a task from its `depends_on` ids and a map of
 * `taskId -> status` for the loaded plan. An id missing from the map can't be
 * confirmed done, so it counts as pending (safe default — never silently
 * unblock a card whose dependency we couldn't see).
 */
export function computeDepState(
  dependsOn: readonly string[] | undefined,
  statusById: ReadonlyMap<string, string>,
): DepState {
  const deps = dependsOn ?? [];
  if (deps.length === 0) {
    return { hasDeps: false, blocked: false, pendingCount: 0 };
  }
  let pendingCount = 0;
  for (const id of deps) {
    if ((statusById.get(id) ?? "") !== DONE) {
      pendingCount += 1;
    }
  }
  return { hasDeps: true, blocked: pendingCount > 0, pendingCount };
}
