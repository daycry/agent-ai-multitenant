"""Vocabulario CERRADO del rechazo del reviewer: `target` x `class` (`task_gov_10`).

Hasta ahora el veredicto de un rechazo era **prosa** (`failed_criterion` /
`what_to_fix` / `testreport_evidence`, ver
`api_server.reviewer_bridge.apply_reviewer_verdict`). Sirve para el reintento
inmediato —el implementador lee qué arreglar— y **no agrega**: nadie puede
responder «¿qué se rechaza más en este proyecto?» ni «¿qué clase de defecto
domina?» sin leer a mano cientos de textos libres.

Este módulo declara los dos ejes, y es la ÚNICA declaración de sus valores:

* **`target`** — QUÉ se rechaza: el código, los tests, el alcance, o el formato
  del entregable.
* **`class`** — POR QUÉ: la clase de defecto.

Dos ejes en vez de una lista plana porque son ortogonales y la combinación
informa más que la suma: `code x overreach` («tocó ficheros fuera del alcance»)
no es `scope x overreach` («implementó algo que nadie pidió»), y una lista plana
obligaría a inventar una etiqueta por cada cruce.

Vive en `shared-domain` por la misma razón que
`shared_domain.approval_categories`: el vocabulario tiene DOS consumidores que
deben coincidir y que no comparten proceso — el sandbox `agent-runtime`, que lo
ANUNCIA en el prompt del reviewer
(`agent_runtime.review_contract.REJECT_TAXONOMY_INSTRUCTION`, derivado de estos
enums), y la api-server, que lo PARSEA y lo persiste
(`api_server.reviewer_bridge`). El runtime no lleva `api_server` dentro, así que
si el vocabulario viviera allí las dos mitades se bifurcarían — que es
exactamente lo que le pasó a las 13 categorías de aprobación antes del hallazgo
g6, cuando el runtime emitía cuatro categorías que no intersecaban con ninguna
política y NADA se gateaba.

## Qué NO es esto (leer antes de reabrir SkillOpt)

Estos dos ejes describen **el trabajo rechazado**. El informe del 2026-08-12
(§2.4) proponía unos ejes distintos con los mismos nombres: `target` como
PUNTERO al objeto a corregir (`skills/X` | `agents/Y`) y `class` como
`rule_missing | rule_wrong | rule_ignored`. Eso es la mitad barata de **SkillOpt**
—el bucle que convierte rechazos repetidos en parches a las instrucciones del
agente—, y el operador lo **aplazó con disparador escrito** (decisión 6 del
informe; `task_gov_11`, hoy **ADR 0158**). El enunciado de `task_gov_10` reorientó
el dato para que **sirva por sí solo aunque ese bucle no llegue nunca**, y es el
que se ha implementado.

**Aviso para quien llegue aquí a reabrirlo**: el disparador de aquel aplazamiento
**saltó el 2026-08-20** (`task_gov_02` + `task_gov_05` cerradas), así que el ADR
0158 está vencido y la decisión está por reabrir — que no es lo mismo que
aprobada.

Consecuencia práctica, y la razón de que esto esté escrito aquí: el día que
SkillOpt se reabra, su reflexión (`skills/X` x `rule_missing`) es un par
**ADITIVO** y no una redefinición de estos dos ejes. `code x incorrect` no dice
qué regla le falta al reviewer de CI4; saber lo uno no es saber lo otro.

## Por qué NO hay bucket «otros»

La decisión explícita de `task_gov_10`: **lo genérico se descarta en vez de
guardarse**. Una etiqueta `other` se lleva la mayoría de los casos en cuanto el
modelo duda, y entonces el eje deja de informar mientras sigue pareciendo que
mide algo. Una etiqueta que no encaja en el vocabulario se DESCARTA (ver
`GENERIC_LABELS` y `normalise_targets` / `normalise_classes`): el rechazo se
queda sin clasificar, el agregado lo cuenta como `unlabelled` y ese número es
justamente la medida de honestidad del dato. Un agregado con el 60 % en «otros»
miente; un agregado con el 60 % sin clasificar lo dice.

## Por qué no hay CHECK en base de datos

El patrón de esta casa para cerrar un value-set es enum + CHECK derivado del
enum (`ck_skills_category`, ADR 0050; `tools.category`, ADR 0049). Aquí no
aplica porque el par NO aterriza en una columna: el veredicto del reviewer se
persiste como `payload` JSONB de un evento `task_audit_events` de
`kind='review_comment'`, que es la fila que ya existe para esto. El precedente
del value-set cerrado que vive en JSONB y se blinda con un test de contrato —no
con un CHECK— es `shared_domain.approval_categories` (13 categorías dentro de
mapas JSONB de política). El cierre lo garantiza el ESCRITOR: nada que no salga
de `normalise_*` llega al payload, y `tests/unit/test_reject_taxonomy.py` lo
fija.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable

__all__ = [
    "GENERIC_LABELS",
    "MAX_LABELS_PER_VERDICT",
    "REJECT_CLASSES",
    "REJECT_CLASS_CLOSE",
    "REJECT_CLASS_OPEN",
    "REJECT_CLASS_TAG",
    "REJECT_TARGETS",
    "REJECT_TARGET_CLOSE",
    "REJECT_TARGET_OPEN",
    "REJECT_TARGET_TAG",
    "RejectClass",
    "RejectTarget",
    "describe_classes",
    "describe_targets",
    "normalise_classes",
    "normalise_targets",
    "reject_taxonomy_instruction",
]


class RejectTarget(enum.StrEnum):
    """QUÉ se rechaza. Cuatro valores, los cuatro de `task_gov_10`.

    Son las cuatro cosas que un reviewer de esta plataforma puede juzgar, y se
    distinguen porque cada una manda a un sitio distinto al arreglarla:
    """

    #: El cambio en sí: lógica, integración, multi-tenancy, seguridad.
    CODE = "code"
    #: Las pruebas: faltan, no cubren el caso, o pasan sin probar nada.
    TESTS = "tests"
    #: Lo que se hizo respecto de lo que se pidió: de más, de menos, u otra cosa.
    SCOPE = "scope"
    #: La FORMA del entregable: el informe, el formato de salida, la doc, el
    #: mensaje de commit — lo que la plataforma o el criterio exigían entregar.
    DELIVERABLE = "deliverable"


class RejectClass(enum.StrEnum):
    """POR QUÉ se rechaza. Seis clases de defecto, disjuntas por intención.

    El criterio para que una clase exista: que dos rechazos de clases distintas
    pidan acciones DISTINTAS al implementador. Si dos etiquetas llevan al mismo
    arreglo, sobra una.
    """

    #: Hace lo que se pidió, pero mal (bug, lógica errónea, caso no manejado).
    INCORRECT = "incorrect"
    #: Falta parte de lo pedido; lo que hay no está mal, está a medias.
    INCOMPLETE = "incomplete"
    #: No hay evidencia de que funcione (test-report ausente, inconcluyente, o
    #: una prueba que el default ya cumplía).
    UNPROVEN = "unproven"
    #: Rompe algo que ya funcionaba.
    REGRESSION = "regression"
    #: Se desvía de un contrato declarado: firma, esquema, convención del repo,
    #: formato de salida acordado.
    CONTRACT_DRIFT = "contract_drift"
    #: Hace MÁS de lo que se pidió, o toca lo que no le tocaba.
    OVERREACH = "overreach"


#: Los dos value-sets, en orden estable (los prompts los interpolan).
REJECT_TARGETS: tuple[str, ...] = tuple(t.value for t in RejectTarget)
REJECT_CLASSES: tuple[str, ...] = tuple(c.value for c in RejectClass)

#: Tope por veredicto, en CADA eje. Tres es la frontera entre «clasificado» y
#: «etiquetado con todo por si acaso»: un rechazo que toca los cuatro targets no
#: informa de nada al agregarlo, y el reviewer que lo emite está describiendo un
#: cambio demasiado grande para revisar, no un defecto.
MAX_LABELS_PER_VERDICT = 3

#: Genéricos que se DESCARTAN explícitamente en vez de mapearse a un valor.
#:
#: Están enumerados —y no simplemente «lo que no esté en el enum se cae», que
#: también ocurre— porque son las formas concretas en que un modelo intenta
#: rellenar el hueco cuando no sabe qué poner. Tenerlos por nombre permite que
#: el test afirme que NINGUNA de ellas cuela, y deja constancia de la decisión:
#: aquí no hay bucket «otros» ni se va a añadir por la puerta de atrás.
GENERIC_LABELS: frozenset[str] = frozenset(
    {
        "other",
        "others",
        "otro",
        "otros",
        "misc",
        "miscellaneous",
        "general",
        "generic",
        "varios",
        "various",
        "quality",
        "calidad",
        "unknown",
        "desconocido",
        "n/a",
        "na",
        "none",
        "ninguno",
        "todo",
        "all",
        "todos",
    }
)

# Alias de UNA sola forma cada uno: singular/plural y la traducción literal que
# el reviewer usa cuando el prompt va en castellano. NO se admiten sinónimos
# «interpretativos» (`bug` → `incorrect`, `missing` → `incomplete`): eso sería
# el bucket genérico disfrazado, decidiendo por el modelo lo que el modelo no
# dijo. Un alias solo existe si ambas formas nombran EL MISMO valor.
_TARGET_ALIASES: dict[str, str] = {
    "codigo": RejectTarget.CODE.value,
    "código": RejectTarget.CODE.value,
    "test": RejectTarget.TESTS.value,
    "tests": RejectTarget.TESTS.value,
    "alcance": RejectTarget.SCOPE.value,
    "entregable": RejectTarget.DELIVERABLE.value,
    "deliverables": RejectTarget.DELIVERABLE.value,
}
_CLASS_ALIASES: dict[str, str] = {
    "incorrecto": RejectClass.INCORRECT.value,
    "incompleto": RejectClass.INCOMPLETE.value,
    "regresion": RejectClass.REGRESSION.value,
    "regresión": RejectClass.REGRESSION.value,
    "contract-drift": RejectClass.CONTRACT_DRIFT.value,
    "contract drift": RejectClass.CONTRACT_DRIFT.value,
}

# Separadores con los que un modelo lista etiquetas: coma, punto y coma, barra,
# `y`/`and` no — un `and` dentro de una etiqueta compuesta no existe en este
# vocabulario, así que partir por él solo produciría ruido.
_SPLIT_RE = re.compile(r"[,;/|\n]+")


def _normalise(
    raw: str | Iterable[str],
    *,
    allowed: frozenset[str],
    aliases: dict[str, str],
) -> tuple[str, ...]:
    """Etiquetas válidas, deduplicadas en orden de aparición y topadas.

    Tolerante en la FORMA (mayúsculas, espacios, separadores, el guion en vez
    del guion bajo) y estricta en el VALOR: lo que no sea del vocabulario se
    descarta en silencio. La tolerancia de forma es la misma postura que
    `reviewer_bridge._normalise_verdict` ya tomó con el tag `<verdict>` — la
    deriva de redacción de los modelos no-Claude es un hecho medido, y perder
    una etiqueta correcta por un espacio de más sería tirar el dato.
    """
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = _SPLIT_RE.split(raw)
    else:
        for item in raw:
            pieces.extend(_SPLIT_RE.split(str(item)))

    out: list[str] = []

    def _resolve(token: str) -> str | None:
        if not token or token in GENERIC_LABELS:
            return None
        alias = aliases.get(token) or aliases.get(token.replace("_", " "))
        if alias is not None:
            return alias
        candidate = token.replace("-", "_").replace(" ", "_")
        return candidate if candidate in allowed else None

    def _take(canonical: str | None) -> bool:
        """Añade la etiqueta; devuelve True cuando el tope se ha alcanzado."""
        if canonical is not None and canonical not in out:
            out.append(canonical)
        return len(out) >= MAX_LABELS_PER_VERDICT

    for piece in pieces:
        token = piece.strip().strip(".·-—*[]()").lower()
        resolved = _resolve(token)
        if resolved is None and " " in token:
            # Última red: una lista separada por ESPACIOS («code tests»). Se
            # intenta después del token entero, no antes, para que un valor de
            # dos palabras (`contract drift`) gane a su propio despiece — al
            # revés, «contract» y «drift» se caerían los dos y perderíamos una
            # etiqueta que el modelo sí había dicho bien.
            for word in token.split():
                if _take(_resolve(word.strip(".·-—*[]()"))):
                    return tuple(out)
            continue
        if _take(resolved):
            break
    return tuple(out)


def normalise_targets(raw: str | Iterable[str]) -> tuple[str, ...]:
    """Los `target` válidos de un veredicto, como máximo `MAX_LABELS_PER_VERDICT`.

    `()` cuando nada encaja — y eso es un resultado legítimo, no un error: el
    rechazo se queda sin clasificar y el agregado lo cuenta aparte.
    """
    return _normalise(raw, allowed=frozenset(REJECT_TARGETS), aliases=_TARGET_ALIASES)


def normalise_classes(raw: str | Iterable[str]) -> tuple[str, ...]:
    """Las `class` válidas de un veredicto, como máximo `MAX_LABELS_PER_VERDICT`."""
    return _normalise(raw, allowed=frozenset(REJECT_CLASSES), aliases=_CLASS_ALIASES)


def describe_targets() -> str:
    """Los `target` con su glosa de una línea, para interpolar en un prompt."""
    return "\n".join(f"  - {t}: {_TARGET_HELP[t]}" for t in REJECT_TARGETS)


def describe_classes() -> str:
    """Las `class` con su glosa de una línea, para interpolar en un prompt."""
    return "\n".join(f"  - {c}: {_CLASS_HELP[c]}" for c in REJECT_CLASSES)


# Las glosas que ve el modelo. Viven aquí y no en el prompt para que el prompt
# no pueda describir un valor que el parser no acepta: `describe_*` recorre el
# enum, así que añadir un valor sin glosa es un KeyError inmediato, no una
# divergencia silenciosa que se descubre meses después leyendo agregados.
_TARGET_HELP: dict[str, str] = {
    RejectTarget.CODE.value: "the change itself (logic, integration, tenancy, security)",
    RejectTarget.TESTS.value: "the tests (missing, not covering the case, vacuously passing)",
    RejectTarget.SCOPE.value: (
        "what was built vs what was asked (too much, too little, or something else)"
    ),
    RejectTarget.DELIVERABLE.value: (
        "the SHAPE of what was handed in (report, output format, docs, commit message)"
    ),
}
_CLASS_HELP: dict[str, str] = {
    RejectClass.INCORRECT.value: "does what was asked, but wrongly (bug, unhandled case)",
    RejectClass.INCOMPLETE.value: "part of what was asked is missing; what exists is half-done",
    RejectClass.UNPROVEN.value: "no evidence it works (test report absent or inconclusive)",
    RejectClass.REGRESSION.value: "breaks something that used to work",
    RejectClass.CONTRACT_DRIFT.value: (
        "deviates from a declared contract (signature, schema, repo convention, format)"
    ),
    RejectClass.OVERREACH.value: "does MORE than asked, or touches what it should not",
}


# ---------------------------------------------------------------------------
# El wire-format: los dos tags y la instruccion que los anuncia
# ---------------------------------------------------------------------------
# Viven aqui, junto al vocabulario, y NO en el prompt ni en el regex del parser,
# porque el modo de fallo de esta feature es exactamente ese: el anuncio y el
# parseo escritos a mano en sitios distintos, que derivan y dejan de casar. Ya
# paso con el tag `<verdict>`, deletreado literalmente en CINCO prompts (hallazgo
# H3, 2026-07-07), y con las 13 categorias de aprobacion (hallazgo g6).
#
# Con esto, las tres piezas salen de la MISMA cadena:
#   * el prompt del reviewer las interpola
#     (`agent_runtime.review_contract` y el seed `builtin_agents`);
#   * el parser construye sus regex desde `REJECT_TARGET_TAG` /
#     `REJECT_CLASS_TAG` (`api_server.reviewer_bridge`).
REJECT_TARGET_TAG = "reject_target"
REJECT_CLASS_TAG = "reject_class"

REJECT_TARGET_OPEN = f"<{REJECT_TARGET_TAG}>"
REJECT_TARGET_CLOSE = f"</{REJECT_TARGET_TAG}>"
REJECT_CLASS_OPEN = f"<{REJECT_CLASS_TAG}>"
REJECT_CLASS_CLOSE = f"</{REJECT_CLASS_TAG}>"


def reject_taxonomy_instruction() -> str:
    """La instruccion que anuncia los dos ejes, derivada del enum.

    Se construye en una funcion y no como constante de modulo para que las
    glosas (`_TARGET_HELP` / `_CLASS_HELP`) esten ya definidas: un valor nuevo
    del enum sin glosa revienta con `KeyError` al primer uso, en vez de colarse
    como una linea que el modelo no entiende.
    """
    return (
        "Inside the <rejection> block, also emit the two CLOSED labels below "
        f"— at most {MAX_LABELS_PER_VERDICT} per axis, comma-separated, and ONLY "
        "values from these lists. Anything else is DISCARDED, and there is no "
        "'other' bucket: if none applies, leave the tag out entirely rather than "
        "inventing a value.\n"
        f"  {REJECT_TARGET_OPEN}what you are rejecting{REJECT_TARGET_CLOSE}\n"
        f"{describe_targets()}\n"
        f"  {REJECT_CLASS_OPEN}why{REJECT_CLASS_CLOSE}\n"
        f"{describe_classes()}"
    )
