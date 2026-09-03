"""Quién abrió qué host queda escrito (`task_mk_02`, ADR 0165 D5).

`platform_settings` guarda sólo `updated_by`/`updated_at` del ÚLTIMO escritor:
cada cambio sobrescribe el rastro del anterior, que es justo lo que aquí hay que
poder auditar. Un host de egress abierto y cerrado tres veces en un mes deja, en
esa tabla, exactamente una línea.

Por eso el cambio de la allowlist escribe además una fila en `audit_log` con el
delta —qué entró y qué salió—, que es la pregunta que se hace en una revisión de
seguridad: no «cómo está ahora» sino «quién lo abrió y cuándo».
"""

from __future__ import annotations

import pytest
from api_server.egress.audit import allowlist_delta

pytestmark = pytest.mark.unit


def test_el_delta_dice_lo_que_entra_y_lo_que_sale() -> None:
    delta = allowlist_delta(["a.example.com", "b.example.com"], ["b.example.com", "c.example.com"])

    assert delta == {"added": ["c.example.com"], "removed": ["a.example.com"]}


def test_sin_cambios_no_hay_delta() -> None:
    """Un PUT que reescribe el mismo valor no ensucia la auditoría: si cada
    guardado dejase fila, la tabla dejaría de servir para encontrar el cambio."""
    assert allowlist_delta(["a.example.com"], ["a.example.com"]) is None


def test_el_orden_de_entrada_no_altera_el_delta() -> None:
    delta = allowlist_delta(["b.example.com", "a.example.com"], ["a.example.com"])

    assert delta == {"added": [], "removed": ["b.example.com"]}


def test_desde_vacio_todo_es_alta() -> None:
    delta = allowlist_delta([], ["mcp.atlassian.com"])

    assert delta == {"added": ["mcp.atlassian.com"], "removed": []}


def test_vaciar_la_lista_es_una_baja_y_se_registra() -> None:
    """La revocación es el caso que más importa auditar, y el que el ADR marca
    como asimétrico: quitar el host del ajuste no cierra el egress hasta que
    alguien aplica el cambio en el proxy."""
    delta = allowlist_delta(["mcp.atlassian.com"], [])

    assert delta == {"added": [], "removed": ["mcp.atlassian.com"]}
