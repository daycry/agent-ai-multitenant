/**
 * Thin, typed TypeScript client for the Agentic Platform public v1 API.
 *
 * Hand-written (NOT generated) layer that wires the GENERATED model types
 * (`./generated`, produced by openapi-typescript-codegen from the committed
 * v1 OpenAPI 3.1 spec) to the `/api/v1` endpoints. It is deliberately small
 * and stable; the MODELS are regenerated from the spec (`scripts/generate.mjs`),
 * the wiring is maintained by hand against them.
 *
 * Why hand-write the client wiring? openapi-typescript-codegen DOES generate
 * a `fetch` service, but it does not honour the spec's `apiKey` (`X-API-Token`
 * header) security scheme — it only injects `Authorization: Bearer <TOKEN>`
 * and otherwise treats `X-API-Token` as a per-call parameter you must pass on
 * every method. That is poor ergonomics for "configure the token once". So we
 * keep the generated TYPES and provide a small client that configures the
 * `X-API-Token` header ONCE (Plan 13 Decisiones Clave: token in header, never
 * a query param) and exposes typed endpoint methods. This mirrors the Python
 * SDK (task_13_13): generated models + a thin hand-written client.
 */
import type {
  ConversationResponse,
  KnowledgeBaseResponse,
  PlanResponse,
  ProjectResponse,
  TaskResponse,
  V1ConversationCreateRequest,
  V1KnowledgeBaseCreateRequest,
  V1PlanCreateRequest,
  V1ProjectCreateRequest,
  V1TaskCreateRequest,
} from "./generated";

/**
 * The header carrying the per-tenant credential. Must match the server's
 * Fase A `X-API-Token` dependency and the `ApiTokenAuth` security scheme in
 * the published OpenAPI document exactly.
 */
export const API_TOKEN_HEADER = "X-API-Token";

/** Default User-Agent so server-side audit can attribute calls to the SDK. */
const USER_AGENT = "agentic-platform-typescript-sdk/0.1.0";

/** The `fetch` implementation the client uses (injectable for tests). */
export type FetchLike = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  },
) => Promise<{
  status: number;
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}>;

/** Options for constructing an {@link ApiClient}. */
export interface ApiClientOptions {
  /** Base URL of the platform, e.g. `https://platform.example.com`. */
  baseUrl: string;
  /** Per-tenant API token sent verbatim in the `X-API-Token` header. */
  apiToken: string;
  /**
   * Custom `fetch` implementation. Defaults to the global `fetch`. Inject a
   * mock in tests to assert headers / paths without a running server.
   */
  fetch?: FetchLike;
}

/** Pagination bounds shared by every list endpoint (the spec's `limit`/`offset`). */
export interface PageParams {
  limit?: number;
  offset?: number;
}

/**
 * A non-2xx response from the public v1 API. Carries the HTTP `statusCode`
 * and the parsed JSON `body` (or raw text) so callers can branch on 401
 * (bad/absent token), 403 (scope), 404 (cross-tenant / missing) or 429 (rate
 * limited) without re-reading the response.
 */
export class ApiError extends Error {
  readonly statusCode: number;
  readonly body: unknown;

  constructor(statusCode: number, body: unknown) {
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    super(`v1 API error ${statusCode}: ${JSON.stringify(detail)}`);
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.body = body;
  }
}

/**
 * Typed client for the Agentic Platform public v1 API.
 *
 * @example
 * ```ts
 * import { ApiClient } from "@agentic-platform/sdk";
 *
 * const api = new ApiClient({
 *   baseUrl: "https://platform.example.com",
 *   apiToken: "tkn_...",
 * });
 * const projects = await api.listProjects({ limit: 50 });
 * ```
 */
export class ApiClient {
  private readonly baseUrl: string;
  private readonly apiToken: string;
  private readonly fetchImpl: FetchLike;

  constructor(options: ApiClientOptions) {
    if (!options.baseUrl) {
      throw new Error("baseUrl is required");
    }
    if (!options.apiToken) {
      throw new Error("apiToken is required");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiToken = options.apiToken;
    const injected = options.fetch;
    if (injected !== undefined) {
      this.fetchImpl = injected;
    } else if (typeof globalThis.fetch === "function") {
      // Bind so the global fetch keeps its expected `this`.
      this.fetchImpl = globalThis.fetch.bind(globalThis) as unknown as FetchLike;
    } else {
      throw new Error("no global fetch available; pass options.fetch");
    }
  }

  // -- request plumbing ----------------------------------------------------
  private buildUrl(path: string, params?: PageParams): string {
    let url = `${this.baseUrl}${path}`;
    const query: string[] = [];
    if (params?.limit !== undefined) {
      query.push(`limit=${encodeURIComponent(String(params.limit))}`);
    }
    if (params?.offset !== undefined) {
      query.push(`offset=${encodeURIComponent(String(params.offset))}`);
    }
    if (query.length > 0) {
      url += `?${query.join("&")}`;
    }
    return url;
  }

  private async request<T>(
    method: string,
    path: string,
    options?: { params?: PageParams; body?: unknown },
  ): Promise<T> {
    const headers: Record<string, string> = {
      [API_TOKEN_HEADER]: this.apiToken,
      "User-Agent": USER_AGENT,
      Accept: "application/json",
    };
    const init: { method: string; headers: Record<string, string>; body?: string } = {
      method,
      headers,
    };
    if (options?.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await this.fetchImpl(this.buildUrl(path, options?.params), init);
    if (response.status >= 400) {
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        body = await response.text();
      }
      throw new ApiError(response.status, body);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  // ========================================================================
  // Projects
  // ========================================================================
  /** GET /api/v1/projects — the token tenant's projects. */
  listProjects(params?: PageParams): Promise<ProjectResponse[]> {
    return this.request<ProjectResponse[]>("GET", "/api/v1/projects", { params });
  }

  /** GET /api/v1/projects/{project_id}. */
  getProject(projectId: string): Promise<ProjectResponse> {
    return this.request<ProjectResponse>("GET", `/api/v1/projects/${projectId}`);
  }

  /** POST /api/v1/projects (requires a `write`-scope token). */
  createProject(payload: V1ProjectCreateRequest): Promise<ProjectResponse> {
    return this.request<ProjectResponse>("POST", "/api/v1/projects", { body: payload });
  }

  // ========================================================================
  // Plans
  // ========================================================================
  /** GET /api/v1/projects/{project_id}/plans. */
  listPlans(projectId: string, params?: PageParams): Promise<PlanResponse[]> {
    return this.request<PlanResponse[]>("GET", `/api/v1/projects/${projectId}/plans`, { params });
  }

  /** GET /api/v1/plans/{plan_id}. */
  getPlan(planId: string): Promise<PlanResponse> {
    return this.request<PlanResponse>("GET", `/api/v1/plans/${planId}`);
  }

  /** POST /api/v1/projects/{project_id}/plans (requires `write`). */
  createPlan(projectId: string, payload: V1PlanCreateRequest): Promise<PlanResponse> {
    return this.request<PlanResponse>("POST", `/api/v1/projects/${projectId}/plans`, {
      body: payload,
    });
  }

  // ========================================================================
  // Tasks
  // ========================================================================
  /** GET /api/v1/projects/{project_id}/tasks. */
  listTasks(projectId: string, params?: PageParams): Promise<TaskResponse[]> {
    return this.request<TaskResponse[]>("GET", `/api/v1/projects/${projectId}/tasks`, { params });
  }

  /** GET /api/v1/projects/{project_id}/tasks/{task_id}. */
  getTask(projectId: string, taskId: string): Promise<TaskResponse> {
    return this.request<TaskResponse>("GET", `/api/v1/projects/${projectId}/tasks/${taskId}`);
  }

  /** POST /api/v1/projects/{project_id}/tasks (requires `write`). */
  createTask(projectId: string, payload: V1TaskCreateRequest): Promise<TaskResponse> {
    return this.request<TaskResponse>("POST", `/api/v1/projects/${projectId}/tasks`, {
      body: payload,
    });
  }

  // ========================================================================
  // Conversations
  // ========================================================================
  /** GET /api/v1/projects/{project_id}/conversations. */
  listConversations(projectId: string, params?: PageParams): Promise<ConversationResponse[]> {
    return this.request<ConversationResponse[]>(
      "GET",
      `/api/v1/projects/${projectId}/conversations`,
      { params },
    );
  }

  /** GET /api/v1/conversations/{conversation_id}. */
  getConversation(conversationId: string): Promise<ConversationResponse> {
    return this.request<ConversationResponse>("GET", `/api/v1/conversations/${conversationId}`);
  }

  /** POST /api/v1/projects/{project_id}/conversations (requires `write`). */
  createConversation(
    projectId: string,
    payload: V1ConversationCreateRequest,
  ): Promise<ConversationResponse> {
    return this.request<ConversationResponse>(
      "POST",
      `/api/v1/projects/${projectId}/conversations`,
      { body: payload },
    );
  }

  // ========================================================================
  // Knowledge bases
  // ========================================================================
  /** GET /api/v1/kbs — the token tenant's knowledge bases. */
  listKbs(params?: PageParams): Promise<KnowledgeBaseResponse[]> {
    return this.request<KnowledgeBaseResponse[]>("GET", "/api/v1/kbs", { params });
  }

  /** GET /api/v1/kbs/{kb_id}. */
  getKb(kbId: string): Promise<KnowledgeBaseResponse> {
    return this.request<KnowledgeBaseResponse>("GET", `/api/v1/kbs/${kbId}`);
  }

  /** POST /api/v1/kbs (requires a `write`-scope token). */
  createKb(payload: V1KnowledgeBaseCreateRequest): Promise<KnowledgeBaseResponse> {
    return this.request<KnowledgeBaseResponse>("POST", "/api/v1/kbs", { body: payload });
  }
}
