"""Guía humana sobre un run en marcha — el lado servidor (`task_wf_71`).

La única intervención posible era matar el run. Estas son las reglas del canal
que lo sustituye, y las tres que importan son negativas: no se acepta para un
run terminado, no se acumula, y se CONSUME al entregarla.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_the_guidance_is_capped() -> None:
    # Suficiente para redirigir, corto para que no se convierta en un segundo
    # enunciado de la tarea metido a mitad de run.
    from api_server.routers.executions import _MAX_GUIDANCE_CHARS

    assert 500 <= _MAX_GUIDANCE_CHARS <= 4000


def test_only_a_running_execution_accepts_guidance() -> None:
    # Guardar una guía para un run terminado la dejaría ahí sin que nadie la
    # leyera jamás — peor que rechazarla, porque el operador creería haberla
    # entregado.
    import inspect

    from api_server.routers.executions import send_execution_guidance

    source = inspect.getsource(send_execution_guidance)
    assert 'execution.status != "running"' in source
    assert "not_running" in source


def test_the_guidance_replaces_instead_of_accumulating() -> None:
    # Dos correcciones seguidas suelen ser una corrección y su matiz;
    # encadenarlas daría al agente instrucciones contradictorias en un turno.
    import inspect

    from api_server.routers.executions import send_execution_guidance

    source = inspect.getsource(send_execution_guidance)
    assert "execution.pending_guidance = text" in source
    assert "+=" not in source


def test_the_internal_endpoint_consumes_the_guidance() -> None:
    # Si no se borrara, se repetiría cada iteración y el agente re-aplicaría una
    # corrección que ya hizo.
    import inspect

    from api_server.routers.internal_agent import pending_guidance

    source = inspect.getsource(pending_guidance)
    assert "row.pending_guidance = None" in source
    # Con lock de fila: dos iteraciones concurrentes del mismo run no pueden
    # llevarse la misma guía dos veces.
    assert "with_for_update" in source


def test_the_internal_endpoint_is_scoped_by_the_minted_token() -> None:
    # Un run no puede leer la guía de otro: el tenant lo fija el token y la
    # tarea viaja en la petición.
    import inspect

    from api_server.routers.internal_agent import pending_guidance

    source = inspect.getsource(pending_guidance)
    assert "Execution.tenant_id == principal.tenant_id" in source
    assert 'Execution.status == "running"' in source
