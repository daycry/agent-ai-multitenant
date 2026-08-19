"""Versión estable del conjunto de prompts del runtime (`task_wf_52`).

`EvalRun.subject_prompt_version` existe desde el Plan 14 y **nadie lo poblaba**,
así que el dashboard de calidad agrupaba todas las corridas bajo «(sin
versión)»: se podía medir la calidad, pero no atribuirla a un cambio de prompt.
Sin eso, cualquier retoque de prompt se entrega sin poder demostrar que mejora
algo.

Qué se versiona, y por qué así
------------------------------
El TEXTO que viaja al modelo, no el fichero que lo contiene.

* Hashear el módulo entero haría que la versión cambiara con cualquier refactor
  —renombrar una variable, mover una función—, y una versión que se mueve sin
  que se mueva ningún prompt no atribuye nada.
* Hashear solo las constantes de módulo se dejaría fuera la mitad de los
  prompts: los empujones (`nudges.py`) están escritos en línea dentro de las
  funciones que los eligen, no como constantes.

Así que se extraen, por AST, **todos los literales de cadena largos** de los
módulos que hablan con el modelo, excluidos los docstrings. Un retoque de
redacción mueve la versión; mejorar un comentario o renombrar algo, no.

El prompt del AGENTE también cuenta (`task_gov_03`)
---------------------------------------------------
Los tres módulos de `_PROMPT_MODULES` son el ANDAMIAJE del runtime. Durante meses
la etiqueta fue sólo eso: **ni un byte del `system_prompt` del agente**, que es el
PRIMER bloque del preámbulo efectivo y lo que distingue a un backend senior de CI4
de un QA. O sea que dos runs con el mismo `prompt_version` podían haber corrido
con personas completamente distintas, y la etiqueta que existe para atribuir un
cambio de comportamiento **no podía atribuir nada** — el mismo agujero que ya se
pagó una vez con `EvalRun.subject_prompt_version` sin poblar.

`prompt_version()` acepta ahora un **sello** del prompt del agente y lo mezcla en
el sha256. El sello lo produce `agent_prompt_seal` a partir del spec del run, por
dos vías con la misma forma:

* ``agent_prompt_version`` — lo que manda el dispatch: el número de versión de
  `agent_prompt_versions` (`task_gov_02`) más el hash del texto efectivo. Ésta es
  la buena: identifica una FILA concreta del historial, así que un run se puede
  atribuir a una edición con autor y fecha.
* ``agent_persona`` — el propio texto que viaja al modelo, hasheado aquí. Es la
  red de seguridad, y no es opcional: un agente que nunca se editó no tiene fila
  de historial, y sin esta rama sus runs volverían a compartir etiqueta con los de
  cualquier otro agente sin historial. El primer día ése es el caso mayoritario.

Sin sello —run pelado, imagen antigua sin dispatch, agente sin persona— la
etiqueta es exactamente la de antes, byte a byte, así que esto no reescribe el
histórico ni parte el eje del dashboard en dos.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Módulos que declaran texto que viaja al modelo.
_PROMPT_MODULES: tuple[str, ...] = (
    "agent_runtime.providers",  # contratos de sistema (decide / review / assess)
    "agent_runtime.nudges",  # empujones inyectados por turno
    "agent_runtime.review_contract",  # el contrato del veredicto
)

# Umbral para distinguir un PROMPT de una cadena corta (una clave de dict, un
# código de estado, un nombre de tool). Los prompts del runtime son párrafos.
_MIN_PROMPT_CHARS = 80

# Longitud de la etiqueta. 12 hex = 48 bits: de sobra para no colisionar entre
# las decenas de releases de un tenant, y corta para caber en una columna, en
# una URL de filtro y en un eje del dashboard.
_LABEL_CHARS = 12

# Claves del `AGENT_TASK_SPEC` de las que sale el sello del prompt del agente.
_SPEC_VERSION_KEY = "agent_prompt_version"
_SPEC_PERSONA_KEY = "agent_persona"

# Prefijo del sello cuando NO hay número de versión. Distingue «este texto, sin
# fila de historial» de «la versión N de este agente»: son dos afirmaciones
# distintas sobre la misma cadena y no deben colapsar en la misma etiqueta.
_UNVERSIONED_PREFIX = "p:"


def _module_source(module_name: str) -> str | None:
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin:
        return None
    try:
        return Path(spec.origin).read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - módulo empaquetado sin fuente
        return None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Los `Constant` que son docstrings, por id — para no hashearlos.

    Documentar mejor una función no es cambiar un prompt, y si contara, cada
    aclaración en un docstring abriría una «release» nueva en el dashboard.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _literals(source: str) -> list[str]:
    """Los literales de cadena LARGOS del módulo, en orden de aparición.

    Se incluyen las partes estáticas de las f-strings: lo interpolado es dato
    del run, no prompt. Se ordena al final para que mover un prompt dentro de
    su módulo no cuente como cambiarlo.
    """
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            if len(node.value) >= _MIN_PROMPT_CHARS:
                out.append(node.value)
    return out


def prompt_texts() -> list[tuple[str, str]]:
    """Los prompts descubiertos, como `(módulo, texto)` ordenados."""
    found: list[tuple[str, str]] = []
    for module_name in _PROMPT_MODULES:
        source = _module_source(module_name)
        if source is None:
            continue
        found.extend((module_name, text) for text in _literals(source))
    return sorted(found)


def agent_prompt_seal(spec: Mapping[str, Any]) -> str | None:
    """El sello del prompt del AGENTE que viaja en ``spec``, o ``None``.

    Prefiere la versión registrada (``agent_prompt_version``, de `task_gov_02`)
    sobre el texto crudo (``agent_persona``): identifica una FILA del historial,
    con autor y fecha, no sólo un contenido. Las dos ramas comparten el hash del
    mismo texto, así que hablan de lo mismo — lo fija
    ``tests/unit/test_agent_prompt_seal_contract.py``, que compara este hash con
    el que calcula la api-server en el otro lado de la frontera de imágenes.

    ``None`` cuando el spec no trae ninguna de las dos, o las trae vacías:
    entonces `prompt_version` produce la etiqueta histórica y no se inventa una
    distinción donde no hay dato.
    """
    recorded = spec.get(_SPEC_VERSION_KEY)
    if isinstance(recorded, Mapping):
        prompt_hash = str(recorded.get("prompt_hash") or "").strip()
        version = recorded.get("version")
        # `bool` es subclase de `int`: sin descartarlo, un `version: true` de un
        # spec mal formado daría el sello "v1:…" y lo ataría a una versión que no
        # existe.
        if prompt_hash and isinstance(version, int) and not isinstance(version, bool):
            return f"v{version}:{prompt_hash}"
        if prompt_hash:
            return f"{_UNVERSIONED_PREFIX}{prompt_hash}"
    persona = spec.get(_SPEC_PERSONA_KEY)
    if isinstance(persona, Mapping):
        text = str(persona.get("prompt") or "").strip()
        if text:
            return _UNVERSIONED_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return None


def prompt_version(agent_seal: str | None = None) -> str:
    """Etiqueta estable de los prompts de ESTE runtime **y del agente**.

    Determinista entre arranques idénticos —mismo código, misma etiqueta, sin
    depender de la hora ni del orden de importación— y distinta en cuanto se
    toca el texto de cualquier prompt.

    El módulo entra en el hash además del texto: mover un prompt de un módulo a
    otro cambia qué contrato aplica a qué llamada, aunque el texto sea idéntico.

    ``agent_seal`` (de :func:`agent_prompt_seal`) mezcla el prompt del AGENTE.
    Omitirlo da la etiqueta histórica byte a byte, que es lo que mantiene
    comparables los runs anteriores a `task_gov_03`.
    """
    digest = hashlib.sha256()
    for module_name, text in prompt_texts():
        digest.update(module_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
        digest.update(b"\x00")
    if agent_seal:
        # Marcador de dominio propio: sin él, un sello podría hacerse pasar por el
        # nombre de un módulo y dos entradas distintas darían el mismo dígito.
        digest.update(b"\x00agent_prompt\x00")
        digest.update(agent_seal.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:_LABEL_CHARS]
