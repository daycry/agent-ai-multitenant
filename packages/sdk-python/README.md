# Agentic Platform — Python SDK

Official, typed Python client for the **public v1 REST API** (`/api/v1`) of the
Agentic Platform (Plan 13). It is generated **from** the published OpenAPI 3.1
contract, so it always matches the server.

## Install

```bash
pip install -e packages/sdk-python        # from the monorepo
# or, once published to the internal registry:
pip install agentic-platform-sdk
```

Runtime deps: `httpx` + `pydantic` v2.

## Usage

```python
from agentic_platform_sdk import ApiClient, V1ProjectCreateRequest

# base URL of the platform + a per-tenant API token (X-API-Token).
with ApiClient("https://platform.example.com", "tkn_...") as api:
    # list (paginated)
    for project in api.list_projects(limit=50, offset=0):
        print(project.id, project.name, project.status)

    # get one
    project = api.get_project("11111111-1111-1111-1111-111111111111")

    # create (needs a `write`-scope token)
    created = api.create_project(V1ProjectCreateRequest(name="My project"))

    # plans / tasks / conversations are project-scoped
    plans = api.list_plans(created.id)
    tasks = api.list_tasks(created.id)
    convs = api.list_conversations(created.id)

    # knowledge bases are tenant-scoped
    kbs = api.list_kbs()
```

The token is sent verbatim in the **`X-API-Token` header** on every request
(never a query parameter). It scopes every call to its own tenant — a `read`
token may only `GET`, a `write` token may also create.

Non-2xx responses raise `agentic_platform_sdk.ApiError` carrying
`status_code` + parsed `body`, so you can branch on `401` (bad/absent token),
`403` (scope), `404` (cross-tenant / missing) or `429` (rate limited).

## How it is built (reproducibility)

The SDK is **generated from the v1 OpenAPI spec**, not hand-maintained against
the live server:

1. `scripts/generate.py` builds the v1 OpenAPI 3.1 document **in-process** by
   calling `api_server.routers.api_v1.openapi.build_v1_openapi()` — the same
   function the live `/api/v1/openapi.json` endpoint serves — and writes it to
   [`openapi-v1.json`](./openapi-v1.json). No running server is needed.
2. It then runs [`datamodel-code-generator`](https://github.com/koxudaxi/datamodel-code-generator)
   over that spec to (re)write `src/agentic_platform_sdk/models.py` — typed
   Pydantic v2 models for the v1 schemas (projects / plans / tasks /
   conversations / kbs + their enums and the create-request bodies).
3. The thin `httpx`-based client (`src/agentic_platform_sdk/client.py`) wires
   those generated models to the v1 endpoints. It is hand-written (small and
   stable) and typed against the generated models.

Regenerate after any change to the public contract:

```bash
python packages/sdk-python/scripts/generate.py
```

### Why datamodel-code-generator (generator substitution — documented)

The roadmap names `openapi-python-client`. We use **`datamodel-code-generator`**
instead: its output is plain **Pydantic v2** — the same modelling library the
whole platform already uses — rather than an `attrs`-based client that drags in
extra runtime deps and a code style that fights the repo's `ruff-format` /
`mypy strict` hooks. The generated models are clean, importable, and the
generated→typed-client boundary is explicit. This is the sanctioned
"equivalent generator, noted" path from the task brief.

## Linting (generated code is excluded — documented)

`src/agentic_platform_sdk/models.py` is **generated** and follows the
generator's own formatting (single-quoted strings, `Field(..., title=...)`
metadata), not the repo's `ruff-format`/`black`/`mypy strict` conventions. To
keep generated code out of the lint gate while keeping the SDK importable +
tested, the package source dir is **excluded** from the repo linters:

- root [`pyproject.toml`](../../pyproject.toml): `black` `extend-exclude`,
  `ruff` `extend-exclude`, `mypy` `exclude` all list
  `packages/sdk-python/src/agentic_platform_sdk/`.
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml): the `mypy` hook
  `exclude` adds the same path.

The hand-written `client.py` is small and intentionally typed; it lives under
the same generated dir for cohesion, so it shares the exclusion. The SDK is
verified instead by `tests/integration/test_sdk_python.py` (imports, model ⇄
schema parity, client construction, mocked-transport header check, typed
errors) — that test is **not** excluded and runs in CI.
