"""El tope de `list_files` que lee el modelo es el que el runtime aplica.

## Por qué existe este fichero

`list_files` acaba de dejar de mentir: durante meses anunció en su esquema un
`pattern` con default `**/*` —una promesa de búsqueda recursiva— y su
implementación lo ignoraba por completo, devolviendo un listado plano sin
filtrar. Medido en un run real del 2026-09-01: **15 llamadas con patrón no
trivial**, todas con la misma respuesta, sin que nada avisara de que el filtro se
descartaba. El agente probó ocho patrones para encontrar los tests de un proyecto
y no pudo concluir nada.

Al arreglarlo aparecieron **dos números que tienen que ser el mismo**: el tope de
entradas que el runtime devuelve (`_MAX_LIST_ENTRIES`) y el que la descripción
del catálogo le promete al modelo («At most N entries come back»). Una
verificación adversarial comprobó que **nada los cruzaba**: bajar la constante a
50 dejaba los 161 tests del runtime en verde con el catálogo prometiendo 500, y
poner «At most 50» en la descripción con el tope en 500 dejaba en verde la suite
ENTERA (5.861 tests).

Ese hueco es exactamente la forma del defecto original —lo anunciado y lo hecho,
separados y sin nadie mirando— así que se cierra aquí y no se deja al cuidado de
quien recuerde.

## Por qué el número importa, y no es cosmético

El coste de una respuesta se paga en el PROMPT, no en el JSON: el observation se
renderiza dos veces en su propio turno y sigue en la cola de contexto ocho turnos
más. Con el tope en 500 y el árbol real del incidente (11.956 entradas), un
`"**/*"` costaba ~22.000 tokens en su turno y ~104.000 acumulados — el
presupuesto entero del run.
"""

from __future__ import annotations

import re

import pytest
from agent_runtime.file_tools import _MAX_LIST_ENTRIES, WorkspaceFiles

pytestmark = pytest.mark.unit


def _fila_del_catalogo() -> dict:
    """La definición de `list-files` tal como la sirve el catálogo built-in.

    Se importa el MISMO objeto que arma el anuncio para el modelo
    (`workers.agent_tool_schemas` lo lee del código de la imagen, no de la fila
    de la BD), en vez de leer el fichero como texto: si mañana la descripción se
    compone en vez de escribirse literal, este test sigue mirando lo que llega.
    """
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    for tool in BUILTIN_TOOLS:
        slug = getattr(tool, "slug", None) or getattr(tool, "name", None)
        if slug in {"list-files", "list_files"}:
            return {
                "description": getattr(tool, "description", "") or "",
                "input_schema": getattr(tool, "input_schema", {}) or {},
            }
    raise AssertionError("no se encontró `list-files` en BUILTIN_TOOLS")


def test_la_descripcion_anuncia_un_tope_concreto() -> None:
    """Sin número anunciado, el test de abajo pasaría en vacío."""
    descripcion = _fila_del_catalogo()["description"]
    assert re.search(r"[Aa]t most\s+(\d+)\s+entries", descripcion), (
        "la descripción de `list-files` ya no anuncia un tope de entradas: o se "
        f"reescribió con otras palabras (y hay que actualizar este test), o se "
        f"perdió la promesa. Descripción actual: {descripcion!r}"
    )


def test_el_tope_anunciado_es_el_que_se_aplica() -> None:
    """Los dos números son el mismo, o el contrato vuelve a mentir."""
    descripcion = _fila_del_catalogo()["description"]
    match = re.search(r"[Aa]t most\s+(\d+)\s+entries", descripcion)
    assert match is not None
    anunciado = int(match.group(1))

    assert anunciado == _MAX_LIST_ENTRIES, (
        f"la descripción promete {anunciado} entradas y el runtime aplica "
        f"{_MAX_LIST_ENTRIES}. El modelo sólo lee la descripción: con estos dos "
        "números separados, o cree que se le ocultan resultados que sí vienen, o "
        "confía en un tope que no es el real. Cambia LOS DOS en el mismo commit."
    )


def test_el_tope_se_aplica_de_verdad(tmp_path) -> None:
    """Y que el número no sea sólo una constante decorativa.

    Comprobar la igualdad de arriba sin esto dejaría pasar un runtime que anuncia
    150, declara 150 y devuelve todo.
    """
    for i in range(_MAX_LIST_ENTRIES + 10):
        (tmp_path / f"f{i:05d}.txt").write_text("x", encoding="utf-8")

    res = WorkspaceFiles(root=str(tmp_path)).file_list({"path": ".", "pattern": "*"})

    assert res.ok, res.error
    salida = res.output or {}
    assert len(salida["entries"]) == _MAX_LIST_ENTRIES, (
        f"se devolvieron {len(salida['entries'])} entradas con el tope en "
        f"{_MAX_LIST_ENTRIES}: la constante no se está aplicando"
    )
    assert salida.get("truncated") is True, (
        "se recortó la respuesta y `truncated` no lo dice: un truncado silencioso "
        "es el mismo defecto que este fichero persigue — el agente creería que no "
        "hay más ficheros"
    )
    assert salida.get("total_matches") == _MAX_LIST_ENTRIES + 10, (
        "`total_matches` no dice cuántas había de verdad, así que el agente no "
        "puede saber cuánto se le está ocultando"
    )
