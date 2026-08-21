"""El diff entre dos versiones del prompt de un agente (`task_gov_02`).

Puro y sin BD a propósito: recibe los valores crudos de dos filas de
`agent_prompt_versions` y devuelve texto. Así el «con diff» del enunciado se
puede probar sin levantar una base, y el endpoint se queda siendo sólo la
consulta.

## Qué se diffea, y por qué no basta el `system_prompt`

Una versión son DOS cosas: el campo plano `agents.system_prompt` y el diccionario
bilingüe `model_config.system_prompts`. Diffear sólo el plano dejaría ediciones
enteras invisibles —cambiar `system_prompts.es` es lo que de verdad mueve lo que
lee el modelo en un agente bilingüe— y diffear sólo el texto EFECTIVO dejaría
invisible el otro idioma, que es un cambio real que alguien hizo y que mañana
puede pasar a ser el efectivo.

Así que se diffea un renderizado canónico de la versión completa, con una sección
por pieza y las lenguas en orden alfabético. El orden fijo importa: si las
secciones salieran en el orden de inserción del JSONB, dos versiones idénticas
podrían producir un diff no vacío y el historial se llenaría de ruido.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

#: Cabecera de la sección del campo plano. Entre corchetes y en una línea propia
#: para que el diff unificado la muestre como contexto y se lea a qué pieza
#: pertenece un cambio.
_FLAT_HEADER = "[system_prompt]"

#: Cuántas líneas de contexto lleva cada hunk. Tres es el default de `diff` y de
#: git; con menos, un cambio de una palabra en un párrafo largo sale sin nada
#: alrededor y no se entiende dónde cae.
_CONTEXT_LINES = 3


def render_prompt_record(system_prompt: str, persona: Mapping[str, Any] | None) -> str:
    """Renderizado canónico de una versión, listo para diffear.

    Determinista: las lenguas van ORDENADAS, no en el orden en que las devuelva
    el JSONB. Sin eso, dos versiones con el mismo contenido y distinto orden de
    claves producirían un diff no vacío.
    """
    lines: list[str] = [_FLAT_HEADER, *(system_prompt or "").splitlines()]
    for lang in sorted((persona or {}).keys()):
        value = (persona or {})[lang]
        lines.append(f"[persona.{lang}]")
        lines.extend(str(value or "").splitlines())
    return "\n".join(lines)


def prompt_version_diff(
    *,
    newer: tuple[str, Mapping[str, Any] | None],
    older: tuple[str, Mapping[str, Any] | None] | None,
) -> str:
    """Diff unificado ``older`` → ``newer``, o ``""`` si no hay nada que mostrar.

    ``older=None`` es la primera versión de la cadena: no tiene contra qué
    compararse, y devolver el prompt entero como si fuera un diff de adición
    duplicaría en la respuesta un texto que la fila ya trae. Cadena vacía.
    """
    if older is None:
        return ""
    left = render_prompt_record(*older).splitlines(keepends=True)
    right = render_prompt_record(*newer).splitlines(keepends=True)
    if left == right:
        return ""
    # `lineterm=""` + el `\n` explícito de abajo: `splitlines(keepends=True)`
    # deja SIN salto la última línea, así que difflib produciría un hunk cuya
    # última línea se pega a la siguiente. Con `lineterm=""` las cabeceras no
    # traen salto propio y lo ponemos nosotros al unir.
    hunks = difflib.unified_diff(
        left,
        right,
        fromfile="anterior",
        tofile="nueva",
        n=_CONTEXT_LINES,
        lineterm="",
    )
    return "\n".join(line.rstrip("\n") for line in hunks)
