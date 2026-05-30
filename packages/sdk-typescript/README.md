# Agentic Platform — TypeScript SDK

Official, typed TypeScript client for the **public v1 REST API** (`/api/v1`) of
the Agentic Platform (Plan 13). It is generated **from** the published OpenAPI
3.1 contract, so it always matches the server.

## Install

```bash
# from the monorepo (workspace package)
npm install   # inside packages/sdk-typescript
```

Zero runtime dependencies: the client uses the platform `fetch` (Node 18+ /
browsers). A custom `fetch` can be injected for tests or non-standard runtimes.

## Usage

```ts
import { ApiClient, type V1ProjectCreateRequest } from "@agentic-platform/sdk";

// base URL of the platform + a per-tenant API token (X-API-Token).
const api = new ApiClient({
  baseUrl: "https://platform.example.com",
  apiToken: "tkn_...",
});

// list (paginated)
const projects = await api.listProjects({ limit: 50, offset: 0 });

// get one
const project = await api.getProject("11111111-1111-1111-1111-111111111111");

// create (needs a `write`-scope token)
const created = await api.createProject({ name: "My project" } satisfies V1ProjectCreateRequest);

// plans / tasks / conversations are project-scoped
const plans = await api.listPlans(created.id);
const tasks = await api.listTasks(created.id);
const convs = await api.listConversations(created.id);

// knowledge bases are tenant-scoped
const kbs = await api.listKbs();
```

The token is sent verbatim in the **`X-API-Token` header** on every request
(never a query parameter). It scopes every call to its own tenant — a `read`
token may only `GET`, a `write` token may also create.

Non-2xx responses throw `ApiError`, which carries `statusCode` + parsed `body`,
so you can branch on `401` (bad/absent token), `403` (scope), `404`
(cross-tenant / missing) or `429` (rate limited).

## How it is built (reproducibility)

The SDK is **generated from the v1 OpenAPI spec**, not hand-maintained against
the live server:

1. `scripts/generate.mjs` builds the v1 OpenAPI 3.1 document **in-process** by
   invoking Python's `api_server.routers.api_v1.openapi.build_v1_openapi()` —
   the same function the live `/api/v1/openapi.json` endpoint serves — and
   writes it to [`openapi-v1.json`](./openapi-v1.json). No running server is
   needed.
2. It then runs [`openapi-typescript-codegen`](https://github.com/ferdikoomen/openapi-typescript-codegen)
   over that spec to (re)write the typed models under `src/generated/` — model
   `type`s + enums for the v1 schemas (projects / plans / tasks / conversations
   / kbs + their create-request bodies). It finally normalizes the generated
   files to LF / no trailing whitespace / final newline so the committed tree
   stays hygiene-clean.
3. The thin client ([`src/client.ts`](./src/client.ts)) wires those generated
   model types to the v1 endpoints. It is hand-written (small and stable) and
   typed against the generated models.

Regenerate after any change to the public contract (run from the repo root with
the dev venv active so Python can import `api_server`):

```bash
node packages/sdk-typescript/scripts/generate.mjs
# on Windows, point PYTHON at the venv interpreter if `python` is not the venv:
#   PYTHON=.venv/Scripts/python.exe node packages/sdk-typescript/scripts/generate.mjs
```

### The generator used (`openapi-typescript-codegen`) + why the client is hand-written

The roadmap names **`openapi-typescript-codegen`** — that is exactly what we use
(v0.30.0, the `fetch` client preset) for the generated model **types**.

We keep only the generated MODELS and provide our own thin client because
`openapi-typescript-codegen` does **not** honour the spec's `apiKey`
(`X-API-Token` header) security scheme: its generated service only injects
`Authorization: Bearer <TOKEN>` and otherwise treats `X-API-Token` as a per-call
parameter you would have to pass on every method. That is poor ergonomics for
"configure the token once". So `src/client.ts` configures the `X-API-Token`
header **once** (Plan 13 Decisiones Clave: token in header, never a query
param) and exposes typed endpoint methods + an injectable `fetch`. This mirrors
the Python SDK (task_13_13): generated models + a thin hand-written client.

## Scripts

| Script              | What it does                                            |
| ------------------- | ------------------------------------------------------- |
| `npm run generate`  | Rebuild spec in-process + regenerate `src/generated/`.  |
| `npm run typecheck` | `tsc --noEmit` over `src/` + `test/` (incl. generated). |
| `npm run build`     | Emit `dist/` (JS + `.d.ts`) from `src/`.                |
| `npm test`          | Run the vitest suite (`npm test -- sdk-typescript`).    |

## Linting (generated code is excluded — documented)

`src/generated/**` is **generated** and follows
`openapi-typescript-codegen`'s own style (4-space indent, `Record<string, any>`,
banner comments), not the repo's `prettier` / `eslint` conventions. To keep
generated code out of the style gate while keeping the SDK importable + tested,
the generated dir is **excluded** from the repo's style linters:

- [`.eslintrc.json`](../../.eslintrc.json): `ignorePatterns` lists
  `packages/sdk-typescript/src/generated/**`.
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml): the `prettier`
  hook `exclude` adds `packages/sdk-typescript/src/generated/.*` and
  `packages/sdk-typescript/openapi-v1.json` (the generated spec, which uses a
  `sort_keys` layout that fights prettier — same as the Python SDK's spec).

The generated dir is **not** excluded from `tsc`: the SDK **type-checks** and
**builds** with the generated models in the program (`npm run typecheck` /
`npm run build`). The universal hygiene hooks (trailing-whitespace, EOF,
mixed-line-ending) are satisfied by the normalization step in
`scripts/generate.mjs`, so the generated dir needs no hygiene-hook exclusion.

The hand-written `src/client.ts` + `src/index.ts` follow the repo's prettier
style and are **not** excluded. The SDK is verified by the vitest suite
([`test/sdk-typescript.test.ts`](./test/sdk-typescript.test.ts)): imports +
public surface, model ⇄ schema parity (compile-time + runtime against the
committed spec), client option validation, a mocked-`fetch` `X-API-Token`
header check, and typed-error handling.
