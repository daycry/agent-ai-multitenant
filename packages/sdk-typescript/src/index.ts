/**
 * Public entry point for the Agentic Platform TypeScript SDK (Plan 13
 * task_13_14).
 *
 * Re-exports the hand-written, typed {@link ApiClient} plus the model TYPES
 * generated from the v1 OpenAPI 3.1 contract (`./generated`). Generated code
 * follows openapi-typescript-codegen's own style and is EXCLUDED from the
 * repo's eslint/prettier (documented in `README.md`); it is reachable here so
 * consumers get the typed models, and is exercised by the package's vitest
 * suite which is NOT excluded.
 */
export {
  API_TOKEN_HEADER,
  ApiClient,
  ApiError,
  type ApiClientOptions,
  type FetchLike,
  type PageParams,
} from "./client";

// Typed models + enums generated from the committed v1 spec.
export type {
  ConversationResponse,
  KnowledgeBaseResponse,
  KbCategorySummary,
  PlanResponse,
  ProjectResponse,
  TaskResponse,
  V1ConversationCreateRequest,
  V1KnowledgeBaseCreateRequest,
  V1PlanCreateRequest,
  V1ProjectCreateRequest,
  V1TaskCreateRequest,
} from "./generated";
export { ChatMode, PlanStatus, ProjectStatus, TaskPriority, TaskStatus } from "./generated";
