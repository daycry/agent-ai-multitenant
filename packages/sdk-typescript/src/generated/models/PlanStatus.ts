/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Full lifecycle of a plan (Plan 03 task_03_16 / task_03_25).
 *
 * Transitions are enforced in `api_server.chat.plan_state_machine`.
 * A freshly POSTed plan from the chat lands in ``draft``; the human
 * moves it to ``pending_approval`` to start the review.
 *
 * When the AI cost estimate exceeds the platform-configured double-
 * signature threshold (task_03_25), the first approval moves the
 * plan to ``pending_second_approval``; a **different** signer must
 * confirm to reach ``approved``. Below the threshold a single firma
 * is enough (``pending_approval -> approved``).
 *
 * Executions of approved plans flip them through ``in_progress``,
 * ``blocked``, then ``pending_human_validation`` and finally
 * ``completed``.
 */
export enum PlanStatus {
    PENDING_APPROVAL = 'pending_approval',
    PENDING_SECOND_APPROVAL = 'pending_second_approval',
    DRAFT = 'draft',
    APPROVED = 'approved',
    IN_PROGRESS = 'in_progress',
    BLOCKED = 'blocked',
    PENDING_HUMAN_VALIDATION = 'pending_human_validation',
    COMPLETED = 'completed',
    CANCELLED = 'cancelled',
    REJECTED = 'rejected',
    ARCHIVED = 'archived',
}
