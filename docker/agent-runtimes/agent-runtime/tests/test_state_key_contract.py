"""H6 (hallazgo #6, QA 2026-07-07): el contrato de claves de AgentState, como test.

``graph.py`` y ``providers.py`` leen/escriben el estado POR STRING
(``state.get("...")`` / ``state["..."]``): renombrar un campo del TypedDict sin
tocar el otro lado compila y rompe en silencio. Este test escanea AMBOS módulos
con AST y exige que TODA clave accedida sobre una variable ``state`` exista en
``AgentState.__annotations__`` (o en la lista explícita de claves inyectadas
ad-hoc). Un rename del TypedDict que olvide un ``state.get("nombre_viejo")``
rompe aquí, no en producción.

Es la versión-guard del hallazgo: convierte el comentario-contrato de
``state.py`` en verificación ejecutable sin tocar el código caliente del loop
(cuya convergencia está calibrada). La migración opcional a constantes/dataclass
queda como pulido futuro.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent_runtime.state import AgentState

_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "agent_runtime"

# Claves que NO están en el TypedDict pero se inyectan deliberadamente en dicts
# derivados del estado (documentadas en state.py). Mantener MÍNIMA.
_INJECTED_KEYS = frozenset({"written_files"})


def _state_keys_accessed(source: str) -> set[str]:
    """Toda clave constante usada como ``state["k"]`` / ``state.get("k", ...)``.

    Considera variables llamadas ``state`` o ``*_state`` (p. ej. ``review_state``)
    para cubrir los dicts derivados que viajan a la self-review.
    """
    tree = ast.parse(source)
    keys: set[str] = set()

    def _is_state_var(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and (node.id == "state" or node.id.endswith("_state"))

    for node in ast.walk(tree):
        # state["k"] — lectura o escritura.
        if isinstance(node, ast.Subscript) and _is_state_var(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        # state.get("k"[, default])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_state_var(node.func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


def test_every_state_key_accessed_by_string_exists_in_the_schema() -> None:
    allowed = set(AgentState.__annotations__) | _INJECTED_KEYS
    for module in ("graph.py", "providers.py"):
        source = (_RUNTIME_DIR / module).read_text(encoding="utf-8")
        accessed = _state_keys_accessed(source)
        unknown = accessed - allowed
        assert not unknown, (
            f"{module} accede a claves de estado que NO existen en AgentState: "
            f"{sorted(unknown)}. ¿Rename silencioso? Actualiza el TypedDict, el "
            "otro módulo y el listado-contrato de state.py."
        )


def test_scanner_detects_a_seeded_unknown_key() -> None:
    seeded = _state_keys_accessed(
        'def f(state):\n    return state.get("no_such_key") or state["output"]\n'
    )
    assert seeded == {"no_such_key", "output"}
    assert "no_such_key" not in set(AgentState.__annotations__)
