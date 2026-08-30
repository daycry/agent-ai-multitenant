"""Con qué se verifica cada criterio, declarado por quien escribió el test.

ADR 0162, **opción A reformulada**. La versión original —«que el planner genere
el comando»— la corrige el propio ADR por una razón estructural: el planner
planifica **antes de que el código exista**, así que pedirle el ``command`` es
pedirle que prediga un nombre de fichero, y un modelo al que se le pide algo que
no puede comprobar escribe algo *plausible*. Un ``--filter LoginTest`` inventado
que falla se lee como «el código está roto», no como «el criterio era ficticio».

Así que A no produce un comando: produce una **DECISIÓN**, una de dos por cada
criterio, y el silencio deja de ser una respuesta válida:

    «esto se verifica ejecutando X»  —o—  «esto no es verificable a máquina, y
    este es el motivo»

La toma el implementador al cerrar, en ``submit_result`` (ADR 0087), porque es el
único actor que lo sabe: acaba de escribir el test. Eso invierte la naturaleza de
la tarea, de **predecir** un nombre de fichero a **reportar** lo que acaba de
correr.

**Vocabulario deliberadamente prestado del worker.** Las claves son las mismas
que ``workers.test_runtime`` ya lee de un criterio de aceptación —``check_type``,
``runtime``, ``command``, ``expected_signal``— y el contador de silencios se
llama igual que allí: ``checks_without_declared_check_type``. No es cosmética.
Inventar un segundo vocabulario para lo mismo es exactamente cómo se acaba con
un estado que nadie produce ni consume, que es el modo de fallo que el ADR 0162
denuncia en su §«Una nota sobre el vocabulario».

**Y esto MIDE, no decide.** Nada de aquí bloquea una tarea, degrada un
``approve`` ni impide que un plan avance: el gate es la opción C y **no está
firmada**. Lo que cambia respecto a ayer no es que se impida algo — es que antes
ni siquiera se podía contar.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# Cuántas declaraciones se aceptan de un FINISH. El tope existe porque esto viaja
# al `steps_log` (un JSONB de auditoría) y lo escribe un modelo: sin techo, una
# respuesta degenerada engorda la fila de la execution sin dar información.
_MAX_DECLARATIONS = 16
_MAX_FIELD_LEN = 500

# El conjunto CERRADO de claves de una declaración, y por qué cada una:
#
#   criterion       de QUÉ criterio habla — sin esto no se puede casar con nada
#   check_type      la decisión: `automated` o cualquier otra cosa (manual/humano)
#   runtime,command con qué se ejecuta; el worker exige LOS DOS para lanzar nada
#   expected_signal la condición del §«La trampa que hay que cerrar CON A»: sin
#                   `tests > 0`, un check puede salir verde sin ejecutar nada
#   reason          por qué NO es automatizable; sin motivo, «manual» es
#                   indistinguible del silencio que esto viene a retirar
_DECLARATION_KEYS = (
    "criterion",
    "check_type",
    "runtime",
    "command",
    "expected_signal",
    "reason",
)

# `<checks>[…]</checks>` — el canal de PROSA, para `claude_sdk`.
#
# Ese proveedor no recibe `submit_result` (un tool call ahí forzaría
# `content=""` y perdería el entregable en prosa), así que su FINISH es texto y
# su estado estructurado ya viaja en un tag: `<finish status="…"/>` (F1.5). La
# declaración usa el mismo camino, ya probado, en vez de inventar otro — y es el
# único por el que la opción A llega al proveedor que corre en la instalación
# viva. `re.S` porque el JSON viene con saltos de línea.
_CHECKS_BLOCK_RE = re.compile(r"<checks>(?P<body>.*?)</checks>", re.IGNORECASE | re.S)


def _normalise_one(raw: Any) -> dict[str, Any] | None:
    """Una declaración limpia, o ``None`` si no dice nada utilizable.

    Dos claves son obligatorias —``criterion`` y ``check_type``— porque sin saber
    de QUÉ criterio habla ni QUÉ declara, la entrada no es una declaración: es
    ruido. Y contar ruido como declaración devolvería al silencio la categoría de
    respuesta válida, que es justo lo que la opción A retira.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in _DECLARATION_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:_MAX_FIELD_LEN]
    if "criterion" not in out or "check_type" not in out:
        return None
    return out


def normalise_declarations(raw: Any) -> tuple[dict[str, Any], ...]:
    """Lo que el modelo mandó, convertido en declaraciones utilizables.

    Tolerante por diseño y **nunca lanza**: lo que llega aquí es texto de un LLM,
    y el entregable de la tarea ya está escrito en el worktree. Una declaración
    mal formada se descarta —el criterio queda NO DECLARADO, que es lo honesto—
    en vez de tumbar un FINISH legítimo.
    """
    if not isinstance(raw, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_DECLARATIONS]:
        declaration = _normalise_one(item)
        if declaration is not None:
            out.append(declaration)
    return tuple(out)


def parse_checks_block(content: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Extraer ``(prosa_sin_bloque, declaraciones)`` de un FINISH en prosa.

    El bloque se **despoja siempre**, se haya podido parsear o no: el ``output``
    de un FINISH ES el entregable de la tarea, y un blob de JSON pegado al final
    lo contamina igual de mal cuando el JSON está roto. Mismo criterio que el tag
    ``<finish/>``, que también se despoja aunque su status sea inválido.
    """
    match = _CHECKS_BLOCK_RE.search(content or "")
    if match is None:
        return content, ()
    stripped = (content[: match.start()] + content[match.end() :]).strip()
    try:
        payload = json.loads(match.group("body"))
    except (ValueError, TypeError):
        _log.warning("<checks> block was not valid JSON; treated as no declaration at all")
        return stripped, ()
    return stripped, normalise_declarations(payload)


def _criterion_key(text: str) -> str:
    """La forma con la que se casa un criterio con su declaración.

    Colapsa espacios y mayúsculas porque quien reescribe el criterio en la
    declaración es un modelo copiando de su propio prompt: exigir igualdad byte a
    byte convertiría cada espacio de más en un «no declarado» falso, que es
    precisamente el falso fallo que manda evitar.
    """
    return " ".join(str(text or "").split()).casefold()


def _criterion_keys(criterion: Any) -> set[str]:
    """Con qué nombres se puede referir el modelo a ESTE criterio: su texto y,
    cuando el criterio es estructurado, también su ``id``."""
    keys: set[str] = set()
    if isinstance(criterion, str):
        keys.add(_criterion_key(criterion))
    elif isinstance(criterion, dict):
        for key in ("description", "text", "criterion", "name", "id"):
            value = criterion.get(key)
            if isinstance(value, str) and value.strip():
                keys.add(_criterion_key(value))
    keys.discard("")
    return keys


def declaration_coverage(
    criteria: list[Any],
    declarations: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Cuántos criterios quedaron sin que nadie dijera cómo se verifican.

    El payload que se persiste en el ``steps_log``. Devuelve el total de
    criterios y ``checks_without_declared_check_type`` — **el mismo nombre que el
    contador del worker**, porque cuenta exactamente lo mismo un piso más arriba:
    criterios cuyo tipo de comprobación nadie declaró.

    Una declaración que no casa con ningún criterio NO descuenta: si el modelo se
    inventa un criterio que no existe, eso no puede hacer desaparecer el silencio
    sobre uno que sí.
    """
    declared: set[str] = {_criterion_key(d.get("criterion", "")) for d in declarations}
    declared.discard("")
    undeclared = sum(1 for c in criteria if not (_criterion_keys(c) & declared))
    return {
        "criteria_total": len(criteria),
        "checks_without_declared_check_type": undeclared,
        "check_declarations": [dict(d) for d in declarations],
    }


__all__ = [
    "declaration_coverage",
    "normalise_declarations",
    "parse_checks_block",
]
