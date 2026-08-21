"""`GET /runtime-templates` — expose the curated runtime-template catalog.

The catalog itself lives in ``shared_test_runtimes.CATALOG`` as 14 frozen
``RuntimeTemplate`` instances (Plan 06 task_06_02). It is static (changes
only when an ADR adds/removes a template), but the admin-panel needs a JSON
view of it to populate the runtime selectors on the project Commands and
Dep-cache screens.

Before Plan 06.18 those selectors hardcoded the catalog *three times* and
diverged (commands/page.tsx listed 14 ids, dep-cache/page.tsx only 12, with
invented labels). This endpoint makes the backend the single source of
truth — the exact same fix already shipped for MCP via ``GET /mcp-catalog``
(ADR 0051, mirrors ADR 0025's pattern).

The endpoint is project-agnostic and tenant-agnostic on purpose: the
catalog is identical across tenants (it ships with the platform, not with a
tenant's data). Per-project state — which runtime a project defaults to —
lives in ``Project.default_runtime_template`` and is validated against this
same catalog in ``schemas/projects.py``.

Auth: any authenticated tenant member can read it.

Labels (ES + EN) are served here, not hardcoded in the frontend, so the
selector text is centralised and consumed identically by the project
screens and by Plan 06.17's Capability Hub. ``RuntimeTemplate`` carries no
human-readable label of its own (it is an execution contract), so the
display names live in :data:`_LABELS` keyed by catalog id; a missing entry
falls back to the id itself rather than 500-ing, and the contract test
keeps the map in lock-step with the catalog.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from shared_test_runtimes import CATALOG
from shared_test_runtimes.types import RuntimeTemplate

from api_server.auth.deps import AuthPrincipal, require_tenant_member

router = APIRouter(prefix="/runtime-templates", tags=["runtime-templates"])


# Display labels per catalog id. The runtime ids are language+framework
# proper nouns, so ES and EN largely coincide; both are served distinctly so
# the frontend never has to invent text and Plan 06.17 can localise without
# round-tripping. Adding a template to the catalog without adding a label
# here is caught by the contract test (it asserts CATALOG.keys() == _LABELS
# keys).
_LABELS: dict[str, dict[str, str]] = {
    "python-pytest": {"es": "Python · pytest", "en": "Python · pytest"},
    "node-jest": {"es": "Node · Jest", "en": "Node · Jest"},
    "node-vitest": {"es": "Node · Vitest", "en": "Node · Vitest"},
    "node-playwright": {"es": "Node · Playwright", "en": "Node · Playwright"},
    "php-phpunit": {"es": "PHP · PHPUnit", "en": "PHP · PHPUnit"},
    "php-pest": {"es": "PHP · Pest", "en": "PHP · Pest"},
    "go-test": {"es": "Go · go test", "en": "Go · go test"},
    "java-maven": {"es": "Java · Maven", "en": "Java · Maven"},
    "java-gradle": {"es": "Java · Gradle", "en": "Java · Gradle"},
    "ruby-rspec": {"es": "Ruby · RSpec", "en": "Ruby · RSpec"},
    "rust-cargo": {"es": "Rust · Cargo", "en": "Rust · Cargo"},
    "dotnet-test": {"es": ".NET · dotnet test", "en": ".NET · dotnet test"},
    "generic-shell": {"es": "Genérico · shell", "en": "Generic · shell"},
    "generic-http": {"es": "Genérico · HTTP", "en": "Generic · HTTP"},
}


class RuntimeLabel(BaseModel):
    """ES + EN display names for a runtime template."""

    es: str
    en: str


class RuntimeTemplateDto(BaseModel):
    """JSON projection of ``shared_test_runtimes.types.RuntimeTemplate``.

    Only the fields the selectors need: ``id`` (the value persisted to
    ``Project.default_runtime_template``), the ES+EN ``label``, the
    ``dep_cache_mount`` (``null`` opts the template out of the caching
    machinery — must round-trip, the front previously dropped those rows),
    and the default ``network_policy``.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: RuntimeLabel
    dep_cache_mount: str | None = Field(
        description="Container path the shared dep-cache mounts at; null = no cache.",
    )
    network_policy: str = Field(
        description="Default container network policy: none | restricted | open.",
    )


def _label_for(template_id: str) -> RuntimeLabel:
    raw = _LABELS.get(template_id)
    if raw is None:
        # Fail soft: an un-labelled template is still selectable by id rather
        # than 500-ing the whole catalog. The contract test guards against
        # this drift in CI.
        return RuntimeLabel(es=template_id, en=template_id)
    return RuntimeLabel(es=raw["es"], en=raw["en"])


def _to_dto(template: RuntimeTemplate) -> RuntimeTemplateDto:
    return RuntimeTemplateDto(
        id=template.id,
        label=_label_for(template.id),
        dep_cache_mount=template.dep_cache_mount,
        network_policy=template.network_policy,
    )


@router.get("", response_model=list[RuntimeTemplateDto])
async def list_runtime_templates(
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> list[RuntimeTemplateDto]:
    """Return every runtime template the platform ships, in the catalog's
    declared insertion order so the admin-panel groups by language without a
    second sort step.
    """
    return [_to_dto(t) for t in CATALOG.values()]


__all__ = ["RuntimeLabel", "RuntimeTemplateDto", "router"]
