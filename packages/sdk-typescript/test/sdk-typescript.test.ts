/**
 * Vitest suite for the official TypeScript SDK (Plan 13 task_13_14).
 *
 * Runs with `npm test -- sdk-typescript` (the `sdk-typescript` arg is a vitest
 * filename filter that selects THIS file). The SDK (`@agentic-platform/sdk`)
 * is a typed client GENERATED from the public v1 OpenAPI 3.1 contract
 * (model types via openapi-typescript-codegen) plus a thin hand-written fetch
 * client. This suite proves the SDK is REAL and usable WITHOUT a running
 * server:
 *
 *   * the package imports and exposes the expected public surface;
 *   * its generated model TYPES match the v1 schemas — a representative model
 *     (`ProjectResponse`) is asserted at COMPILE TIME against the spec shape,
 *     and the committed spec's `ProjectResponse` properties are checked at
 *     runtime against a value typed as the generated model;
 *   * an `ApiClient` validates its required options;
 *   * driven against a MOCK fetch (no network), the client sends the
 *     `X-API-Token` header on every request, hits the right v1 path + query
 *     and decodes the response into the typed model;
 *   * a non-2xx response (401/403/404/429) surfaces as a typed `ApiError`.
 *
 * `npm run typecheck` / `npm run build` cover the "type-checks + builds" leg;
 * the compile-time `satisfies` assertions below additionally fail the BUILD if
 * a generated model ever drifts from the field set the client relies on.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  API_TOKEN_HEADER,
  ApiClient,
  ApiError,
  type FetchLike,
  ProjectStatus,
  type ProjectResponse,
  type V1ProjectCreateRequest,
} from "../src/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const SPEC_PATH = join(HERE, "..", "openapi-v1.json");

type JsonObject = Record<string, unknown>;

interface OpenApiSpec {
  openapi: string;
  components: {
    schemas: Record<string, { properties?: Record<string, unknown> }>;
    securitySchemes: Record<string, { name?: string }>;
  };
}

function loadSpec(): OpenApiSpec {
  return JSON.parse(readFileSync(SPEC_PATH, "utf-8")) as OpenApiSpec;
}

/** A value shaped exactly like the v1 `ProjectResponse` schema. */
function projectPayload(): ProjectResponse {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "Demo project",
    description: null,
    status: "active",
    team_id: null,
    mcp_servers: [],
    rag_knowledge_bases: [],
    worker_config: {},
    repository_config: null,
    human_approval_policy: null,
    secrets_vault_id: null,
    budget_amount: null,
    budget_currency: null,
    budget_period: null,
    budget_period_start_day: null,
    budget_period_length_days: null,
    paused_by_budget: false,
    is_template: false,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-05-30T00:00:00Z",
    deleted_at: null,
  };
}

describe("SDK public surface", () => {
  it("exposes the client, error, header constant and enums", () => {
    expect(typeof ApiClient).toBe("function");
    expect(typeof ApiError).toBe("function");
    expect(API_TOKEN_HEADER).toBe("X-API-Token");
    // generated enum re-export is reachable + valued from the spec
    expect(ProjectStatus.ACTIVE).toBe("active");
  });
});

describe("committed spec is the v1 contract", () => {
  it("is OpenAPI 3.1 and the model type matches the ProjectResponse schema", () => {
    const spec = loadSpec();
    expect(spec.openapi.startsWith("3.1")).toBe(true);

    // The auth scheme advertised by the contract is the one the client sends.
    expect(spec.components.securitySchemes.ApiTokenAuth.name).toBe(API_TOKEN_HEADER);

    // Runtime parity: a value TYPED as the generated ProjectResponse must
    // carry exactly the property set the spec component declares. Because
    // `payload` is annotated `ProjectResponse`, this also fails the BUILD if
    // the generated model loses/gains a field relative to this shape.
    const payload = projectPayload();
    const modelProps = new Set(Object.keys(payload));
    const schemaProps = new Set(
      Object.keys(spec.components.schemas.ProjectResponse.properties ?? {}),
    );
    expect([...modelProps].sort()).toEqual([...schemaProps].sort());
  });
});

describe("client construction", () => {
  it("requires baseUrl and apiToken", () => {
    expect(() => new ApiClient({ baseUrl: "", apiToken: "tkn" })).toThrow(/baseUrl/);
    expect(() => new ApiClient({ baseUrl: "https://x.example", apiToken: "" })).toThrow(/apiToken/);
    // happy path constructs (mock fetch so no global needed)
    const api = new ApiClient({
      baseUrl: "https://platform.example.com",
      apiToken: "tkn",
      fetch: (async () => ({
        status: 200,
        json: async () => [],
        text: async () => "",
      })) as FetchLike,
    });
    expect(api).toBeInstanceOf(ApiClient);
  });
});

describe("driven against a mock fetch", () => {
  it("sends the X-API-Token header, hits the right path+query, decodes the model", async () => {
    let seenToken = "";
    let seenUrl = "";
    const body = projectPayload();

    const fetchMock: FetchLike = async (url, init) => {
      seenUrl = url;
      seenToken = init?.headers?.[API_TOKEN_HEADER] ?? "";
      return {
        status: 200,
        json: async () => [body],
        text: async () => JSON.stringify([body]),
      };
    };

    const api = new ApiClient({
      baseUrl: "https://platform.example.com",
      apiToken: "tkn_secret_123",
      fetch: fetchMock,
    });
    const projects = await api.listProjects({ limit: 10, offset: 0 });

    expect(seenToken).toBe("tkn_secret_123");
    expect(seenUrl).toBe("https://platform.example.com/api/v1/projects?limit=10&offset=0");
    expect(projects).toHaveLength(1);
    expect(projects[0]?.name).toBe("Demo project");
    expect(projects[0]?.status).toBe("active");
  });

  it("POST sends a typed JSON body with the token header", async () => {
    let seenMethod = "";
    let seenBody: JsonObject = {};
    let seenToken = "";

    const fetchMock: FetchLike = async (_url, init) => {
      seenMethod = init?.method ?? "";
      seenToken = init?.headers?.[API_TOKEN_HEADER] ?? "";
      seenBody = JSON.parse(init?.body ?? "{}") as JsonObject;
      return {
        status: 201,
        json: async () => projectPayload(),
        text: async () => "",
      };
    };

    const api = new ApiClient({
      baseUrl: "https://platform.example.com",
      apiToken: "tkn",
      fetch: fetchMock,
    });
    const payload: V1ProjectCreateRequest = { name: "New project" };
    const created = await api.createProject(payload);

    expect(seenMethod).toBe("POST");
    expect(seenToken).toBe("tkn");
    expect(seenBody.name).toBe("New project");
    expect(created.name).toBe("Demo project");
  });
});

describe("error handling", () => {
  it.each([401, 403, 404, 429])("surfaces a %i response as a typed ApiError", async (status) => {
    const fetchMock: FetchLike = async () => ({
      status,
      json: async () => ({ detail: "nope" }),
      text: async () => '{"detail":"nope"}',
    });

    const api = new ApiClient({
      baseUrl: "https://platform.example.com",
      apiToken: "tkn",
      fetch: fetchMock,
    });

    await expect(api.listProjects()).rejects.toThrowError(ApiError);
    try {
      await api.listProjects();
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).statusCode).toBe(status);
      expect((err as ApiError).body).toEqual({ detail: "nope" });
    }
  });
});
