"""Versionado del conjunto de prompts (`task_wf_52`).

`EvalRun.subject_prompt_version` existía desde el Plan 14 y nadie lo poblaba, así
que el dashboard de calidad agrupaba todo bajo «(sin versión)»: se medía la
calidad sin poder atribuirla a un cambio de prompt.

Lo que una etiqueta así tiene que cumplir para servir de algo son dos
propiedades opuestas, y las dos se rompen fácil:

  * **se mueve** cuando cambia el texto que ve el modelo — si no, dos releases
    distintas comparten etiqueta y la comparación miente;
  * **no se mueve** cuando no cambia — si no, cada refactor abre una «release»
    fantasma y el histórico se vuelve ilegible.
"""

from __future__ import annotations

import textwrap

from agent_runtime.prompt_version import _literals, prompt_texts, prompt_version


def test_the_label_is_stable_across_calls() -> None:
    # Determinista: sin reloj, sin aleatoriedad, sin depender del orden de
    # importación. Dos arranques idénticos del mismo código dan lo mismo.
    assert prompt_version() == prompt_version()


def test_the_label_is_short_enough_to_live_in_a_column_and_a_filter() -> None:
    label = prompt_version()
    assert len(label) == 12
    assert all(c in "0123456789abcdef" for c in label)


def test_the_runtime_actually_has_prompts_to_version() -> None:
    # Si el descubrimiento deja de encontrar nada, la etiqueta seguiría siendo
    # estable — sería el hash del vacío — y todos los runs volverían a compartir
    # versión sin que nada fallara. Es el modo de fallo silencioso de esta pieza.
    texts = prompt_texts()
    assert len(texts) >= 10, [name for name, _ in texts]
    modules = {module for module, _ in texts}
    assert "agent_runtime.providers" in modules
    # Los empujones están escritos EN LÍNEA dentro de las funciones que los
    # eligen: si el descubrimiento solo mirase constantes de módulo, la mitad
    # de los prompts no contarían.
    assert "agent_runtime.nudges" in modules


# ---------------------------------------------------------------------------
# Qué mueve la etiqueta y qué no
# ---------------------------------------------------------------------------
def _hash_of(source: str) -> tuple[str, ...]:
    return tuple(_literals(textwrap.dedent(source)))


_BASE = '''
    """Docstring del módulo."""

    _SYSTEM = (
        "Eres un agente de implementación. Trabaja sobre el worktree montado en "
        "/workspace y entrega el resultado con submit_result cuando termines."
    )


    def nudge(count: int) -> str:
        """Docstring de la función."""
        return (
            "Llevas varios turnos leyendo sin escribir nada. Elige un fichero y "
            "empieza a producir el entregable que pide la tarea."
        )
'''


def test_editing_a_prompt_moves_the_label() -> None:
    edited = _BASE.replace("Elige un fichero", "Elige UN fichero concreto")
    assert _hash_of(_BASE) != _hash_of(edited)


def test_editing_an_inline_nudge_moves_it_too() -> None:
    # El caso que una versión «solo de constantes» se dejaría fuera.
    edited = _BASE.replace("empieza a producir", "ponte a producir ya")
    assert _hash_of(_BASE) != _hash_of(edited)


def test_renaming_a_constant_does_not_move_the_label() -> None:
    # Un refactor no es una release. Si contara, el dashboard se llenaría de
    # versiones que no cambian nada de lo que ve el modelo.
    assert _hash_of(_BASE) == _hash_of(_BASE.replace("_SYSTEM", "_DECIDE_SYSTEM"))


def test_editing_a_docstring_does_not_move_the_label() -> None:
    edited = _BASE.replace(
        '"""Docstring de la función."""',
        '"""Docstring de la función, ahora mucho más larga y explicativa, con '
        "detalles sobre por qué existe y cuándo se llama, que es lo que uno "
        'quiere poder mejorar sin abrir una release."""',
    )
    assert _hash_of(_BASE) == _hash_of(edited)


def test_a_short_string_is_not_a_prompt() -> None:
    # Nombres de tool, claves de dict y códigos de estado no son prompts:
    # contarlos haría que la etiqueta se moviera con cualquier cambio de código.
    assert _hash_of(_BASE) == _hash_of(_BASE + '    _TOOL = "write_file"\n')


# ---------------------------------------------------------------------------
# La etiqueta llega al envelope del run
# ---------------------------------------------------------------------------
def test_the_execution_result_carries_the_label() -> None:
    from agent_runtime.graph import ExecutionResult

    result = ExecutionResult(
        status="done",
        abort_code=None,
        output="ok",
        iterations=1,
        steps=[],
        usage={},
        prompt_version=prompt_version(),
    )
    assert result.as_dict()["prompt_version"] == prompt_version()


def test_an_older_result_shape_simply_has_none() -> None:
    # Un run de una imagen anterior al versionado no la trae; el worker la
    # persiste como NULL en vez de inventar una etiqueta, que agruparía runs de
    # prompts distintos bajo la misma release.
    from agent_runtime.graph import ExecutionResult

    result = ExecutionResult(
        status="done", abort_code=None, output="ok", iterations=1, steps=[], usage={}
    )
    assert result.as_dict()["prompt_version"] is None
