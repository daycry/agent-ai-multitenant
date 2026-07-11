"""NOTIF-3: el catálogo de eventos de la UI en sync con el registro real.

El api-server no importa el dispatcher (frontera limpia), así que el catálogo
que sirve la UI de preferencias vive duplicado en `routers/notifications.py`.
Este test es el candado anti-drift: los tests SÍ pueden importar ambos paquetes
y fallan si alguien añade/retira un evento en un lado y no en el otro (la causa
raíz del hardcode desalineado que auditamos: 4 eventos, uno inexistente).
"""

from __future__ import annotations

import pytest
from api_server.routers.notifications import NOTIFICATION_EVENT_CATALOG
from notification_dispatcher.event_mapping import EVENT_REGISTRY

pytestmark = pytest.mark.unit


def test_catalog_matches_dispatcher_registry_exactly() -> None:
    catalog_types = {entry["event_type"] for entry in NOTIFICATION_EVENT_CATALOG}
    assert catalog_types == set(EVENT_REGISTRY)


def test_catalog_entries_carry_both_languages() -> None:
    for entry in NOTIFICATION_EVENT_CATALOG:
        assert entry["label_es"].strip(), entry
        assert entry["label_en"].strip(), entry


def test_retired_or_invented_events_are_gone() -> None:
    # `review_needed` nunca existió; `task_failed` era imposible por diseño.
    catalog_types = {entry["event_type"] for entry in NOTIFICATION_EVENT_CATALOG}
    assert "review_needed" not in catalog_types
    assert "task_failed" not in catalog_types
    assert "task_failed" not in EVENT_REGISTRY
