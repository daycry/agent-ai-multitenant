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
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path

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


def prompt_version() -> str:
    """Etiqueta estable del conjunto de prompts de ESTE runtime.

    Determinista entre arranques idénticos —mismo código, misma etiqueta, sin
    depender de la hora ni del orden de importación— y distinta en cuanto se
    toca el texto de cualquier prompt.

    El módulo entra en el hash además del texto: mover un prompt de un módulo a
    otro cambia qué contrato aplica a qué llamada, aunque el texto sea idéntico.
    """
    digest = hashlib.sha256()
    for module_name, text in prompt_texts():
        digest.update(module_name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:_LABEL_CHARS]
