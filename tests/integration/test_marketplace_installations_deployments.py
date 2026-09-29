"""`GET /marketplace/installations` con «dónde está desplegado» — `task_ui_32`.

Ampliación ADITIVA: cada instalación lleva `deployments: [{project_id,
project_name, status}]` (sólo `active` y `disabled`; un despliegue retirado es
historial y vive en la ficha) sin una consulta por instalación.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.test_marketplace_deployments_api import (
    _client,
    _install,
    _mint,
    _seed,
    configured_app,  # noqa: F401
)

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


@pytest.mark.asyncio
async def test_installations_list_names_the_projects_they_are_deployed_in(
    configured_app: Any,  # noqa: F811 — fixture importada
    migrations_pg_dsn: str,
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    async with _client(configured_app) as client:
        before = await client.get("/marketplace/installations", headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()[0]["deployments"] == []

        for project in ("project_a", "project_a2"):
            created = await client.post(
                f"/marketplace/installations/{installation}/deployments",
                json={"project_id": str(ids[project]), "config": {"base_url": "https://a.test"}},
                headers=headers,
            )
            assert created.status_code == 201, created.text
        retired_id = created.json()["deployment"]["id"]
        await client.post(f"/marketplace/deployments/{retired_id}/retire", headers=headers)

        listed = await client.get("/marketplace/installations", headers=headers)
    body = listed.json()
    assert len(body) == 1
    assert body[0]["listing_name"] == "status-checker"
    assert body[0]["deployments"] == [
        {"project_id": str(ids["project_a"]), "project_name": "API A1", "status": "active"}
    ]


@pytest.mark.asyncio
async def test_installations_list_never_leaks_another_tenants_deployments(
    configured_app: Any,  # noqa: F811 — fixture importada
    migrations_pg_dsn: str,
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    admin_a = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    admin_b = {"Authorization": f"Bearer {await _mint(ids['admin_b'], ids['tenant_b'])}"}
    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=admin_a,
        )
        assert created.status_code == 201, created.text
        other = await client.get("/marketplace/installations", headers=admin_b)
    assert other.status_code == 200
    assert other.json() == []
    assert "API A1" not in other.text
