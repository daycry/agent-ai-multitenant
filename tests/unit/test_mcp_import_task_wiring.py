"""La task del import automático está cableada de punta a punta (ADR 0166 D3, `task_mk_01`).

Tres cosas que, si fallan, producen el mismo síntoma silencioso —el productor
devuelve `True`, el mensaje muere en el broker— y que por eso se fijan aquí en vez
de descubrirse en producción:

1. El nombre y la cola que declara el productor (`api_server.celery_client`) son
   los que declara el consumidor (`workers.mcp_import`).
2. El módulo está en el `imports=` de `celery_app.py`: sin ese import el worker
   arranca sin registrar la task y los mensajes mueren con `NotRegistered`. Es
   la lección escrita en el comentario de esa lista para `marketplace_gates`.
3. El callback de «Conectar» y el despliegue del marketplace encolan con la
   misma firma que el productor acepta.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from api_server import celery_client
from api_server.marketplace import deploy as deploy_module
from api_server.routers import mcp_oauth as oauth_router

_ROOT = Path(__file__).resolve().parents[2]
_CELERY_APP = _ROOT / "apps" / "workers" / "src" / "workers" / "celery_app.py"


def test_productor_y_consumidor_declaran_el_mismo_nombre_y_cola() -> None:
    from workers import mcp_import

    assert mcp_import.TASK_NAME == celery_client.MCP_IMPORT_TASK
    assert mcp_import.QUEUE == celery_client.MCP_IMPORT_QUEUE == "marketplace"
    assert mcp_import.import_server_tools_task.name == celery_client.MCP_IMPORT_TASK


def test_el_modulo_esta_en_el_include_del_worker() -> None:
    src = _CELERY_APP.read_text(encoding="utf-8")
    start = src.index("imports=(")
    # El bloque termina en `task_acks_late`, la siguiente opción de Celery: los
    # comentarios de la lista llevan paréntesis, así que no vale cortar en `)`.
    end = src.index("task_acks_late", start)
    assert re.search(r'"workers\.mcp_import"', src[start:end]) is not None


def test_la_firma_del_productor_cubre_lo_que_mandan_los_dos_disparadores() -> None:
    params = set(inspect.signature(celery_client.enqueue_mcp_import_server_tools).parameters)
    assert {
        "tenant_id",
        "project_id",
        "server_name",
        "installation_id",
        "listing_id",
        "version",
        "roles",
    } <= params
    # El callback de «Conectar» sólo conoce tenant/proyecto/servidor: tienen que
    # bastar (el resto es opcional).
    sig = inspect.signature(celery_client.enqueue_mcp_import_server_tools)
    for optional in ("installation_id", "listing_id", "version", "roles"):
        assert sig.parameters[optional].default is None


def test_el_callback_de_conectar_engancha_el_import() -> None:
    src = inspect.getsource(oauth_router.oauth_callback)
    assert "on_connected=_enqueue_import_after_connect" in src


def test_el_despliegue_encola_tras_el_commit_y_no_en_linea() -> None:
    src = inspect.getsource(deploy_module._queue_import)
    assert "schedule_after_commit" in src
    assert "await publisher(" in src
    # Y `_materialize_mcp_server` ya no se rinde con «vuelve a desplegar».
    body = inspect.getsource(deploy_module._materialize_mcp_server)
    assert "tras importarlas, vuelve a desplegar" not in body
    assert "_queue_import(" in body
