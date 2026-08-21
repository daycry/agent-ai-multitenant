"""El diff del historial del prompt (`task_gov_02`), sin base de datos.

`agent_prompt_diff` es puro a propósito: el «con diff» del enunciado se prueba
aquí, y el endpoint se queda siendo sólo la consulta.

Lo que hay que fijar no es «que devuelve un diff» —eso lo hace `difflib`— sino las
tres decisiones que un `unified_diff` a pelo NO toma y que deciden si el historial
se puede leer:

1. Se diffea la versión COMPLETA (campo plano + las dos lenguas), no el texto
   efectivo. Si no, editar el idioma que la precedencia no prefiere generaría una
   fila con diff vacío: una versión que existe y no se puede explicar.
2. El renderizado es determinista. Con las lenguas en el orden del JSONB, dos
   versiones de idéntico contenido podrían dar un diff no vacío.
3. La primera versión de la cadena no lleva diff. Devolver el prompt entero como
   adición duplicaría en la respuesta un texto que la fila ya trae.
"""

from __future__ import annotations

import pytest
from api_server.agent_prompt_diff import prompt_version_diff, render_prompt_record

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# El renderizado canónico
# ---------------------------------------------------------------------------
def test_the_record_carries_the_flat_field_and_every_language() -> None:
    rendered = render_prompt_record("plano", {"es": "en castellano", "en": "in english"})
    assert "[system_prompt]" in rendered
    assert "plano" in rendered
    assert "[persona.es]" in rendered
    assert "en castellano" in rendered
    assert "[persona.en]" in rendered
    assert "in english" in rendered


def test_the_language_order_does_not_depend_on_the_dict_order() -> None:
    """Determinismo: es el invariante del que cuelga «diff vacío = sin cambios».

    El JSONB de PostgreSQL no promete orden de claves. Sin ordenar, dos versiones
    con el mismo contenido darían un diff con las secciones movidas, y el
    historial se llenaría de ruido que nadie sabría interpretar.
    """
    uno = render_prompt_record("p", {"es": "A", "en": "B"})
    otro = render_prompt_record("p", {"en": "B", "es": "A"})
    assert uno == otro
    assert uno.index("[persona.en]") < uno.index("[persona.es]"), (
        "las lenguas deben ir ordenadas alfabéticamente, no por inserción"
    )


def test_a_missing_persona_renders_only_the_flat_field() -> None:
    # Un agente no bilingüe. `None` y `{}` tienen que dar lo mismo: la columna
    # nace con `'{}'::jsonb` pero un objeto en memoria puede traer `None`.
    assert render_prompt_record("solo plano", None) == render_prompt_record("solo plano", {})


# ---------------------------------------------------------------------------
# El diff
# ---------------------------------------------------------------------------
def test_the_oldest_version_has_no_diff() -> None:
    assert prompt_version_diff(newer=("cualquiera", {}), older=None) == ""


def test_identical_versions_produce_no_diff() -> None:
    """Un diff vacío tiene que significar «no cambió nada».

    Es lo que permite al endpoint no inventarse un campo «changed»: si esto
    devolviera cualquier cosa no vacía para dos versiones iguales, la pantalla
    mostraría un cambio donde no lo hay.
    """
    igual = ("mismo texto", {"es": "misma persona"})
    assert prompt_version_diff(newer=igual, older=igual) == ""


def test_editing_the_flat_prompt_shows_up_as_a_line_change() -> None:
    diff = prompt_version_diff(
        newer=("Eres un QA meticuloso.", {}),
        older=("Eres un QA.", {}),
    )
    assert "-Eres un QA." in diff
    assert "+Eres un QA meticuloso." in diff
    # Y la cabecera dice qué sección: sin ella, un cambio de una línea en un
    # agente bilingüe no diría en qué pieza cayó.
    assert "[system_prompt]" in diff


def test_editing_the_NON_preferred_language_still_shows_up() -> None:
    """El caso que un diff del texto efectivo dejaría invisible.

    Con `es` presente, `resolve_agent_persona` prefiere `es`, así que tocar `en` no
    mueve una coma de lo que ve el modelo HOY. Pero es una edición real que alguien
    hizo, que generó su fila, y que mañana puede pasar a ser la efectiva. Si el
    diff no la mostrara, el historial tendría una versión inexplicable.
    """
    diff = prompt_version_diff(
        newer=("plano", {"es": "sin tocar", "en": "reescrito en inglés"}),
        older=("plano", {"es": "sin tocar", "en": "original en inglés"}),
    )
    assert diff, "una edición del idioma no preferido no puede dar un diff vacío"
    assert "-original en inglés" in diff
    assert "+reescrito en inglés" in diff


def test_adding_a_language_shows_up_as_a_new_section() -> None:
    diff = prompt_version_diff(
        newer=("plano", {"es": "castellano", "en": "english"}),
        older=("plano", {"es": "castellano"}),
    )
    assert "+[persona.en]" in diff
    assert "+english" in diff


def test_a_multiline_prompt_diffs_line_by_line_not_as_one_blob() -> None:
    """Con el prompt entero en una línea, cualquier retoque marcaría todo cambiado.

    Es lo que hace legible el diff de un prompt de veinte párrafos: sólo el
    párrafo tocado sale marcado.
    """
    antes = "linea uno\nlinea dos\nlinea tres\nlinea cuatro\nlinea cinco"
    despues = antes.replace("linea tres", "linea tres RETOCADA")
    diff = prompt_version_diff(newer=(despues, {}), older=(antes, {}))
    assert "-linea tres" in diff
    assert "+linea tres RETOCADA" in diff
    # `linea uno` queda fuera del hunk (3 líneas de contexto la dejan justo dentro,
    # pero SIN marca), y desde luego no sale como cambiada.
    assert "-linea uno" not in diff
    assert "+linea uno" not in diff


def test_no_line_of_the_diff_swallows_the_next_one() -> None:
    """`splitlines(keepends=True)` deja la última línea SIN salto.

    Con el `lineterm` por defecto de difflib, ese detalle pega la última línea de
    un hunk a la cabecera del siguiente y el diff sale ilegible justo donde acaba
    el prompt — el sitio que más se mira.
    """
    diff = prompt_version_diff(
        newer=("uno\ndos\nFINAL NUEVO", {}),
        older=("uno\ndos\nFINAL VIEJO", {}),
    )
    lineas = diff.splitlines()
    assert "+FINAL NUEVO" in lineas, lineas
    assert "-FINAL VIEJO" in lineas, lineas
    # Ninguna línea trae dos marcadores pegados, que es cómo se manifiesta el fallo.
    assert not [ln for ln in lineas if ln.count("@@") > 2]
