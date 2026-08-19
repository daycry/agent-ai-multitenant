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

## Y la mitad que faltaba: el prompt del AGENTE (`task_gov_03`)

Los tres módulos que hasheaba `_PROMPT_MODULES` son el ANDAMIAJE del runtime. El
`system_prompt` del agente —el PRIMER bloque del preámbulo, lo que distingue a un
backend senior de CI4 de un QA— no entraba, ni un byte. Consecuencia exacta: dos
runs con el mismo `prompt_version` podían haber corrido con personas
completamente distintas, así que la etiqueta cumplía la segunda propiedad de
arriba y **fallaba la primera**, que es la que justifica que exista.

Los tests del último bloque fijan la propiedad entera: cambiar el `system_prompt`
cambia el `prompt_version`. Y la contraria, que es la que impide que el arreglo
rompa el histórico: **sin sello, la etiqueta es la de siempre byte a byte**.
"""

from __future__ import annotations

import textwrap

from agent_runtime.prompt_version import (
    _literals,
    agent_prompt_seal,
    prompt_texts,
    prompt_version,
)


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


# ---------------------------------------------------------------------------
# El prompt del AGENTE entra en la etiqueta (`task_gov_03`)
# ---------------------------------------------------------------------------
def _spec(prompt: str, **extra: object) -> dict[str, object]:
    """Un `AGENT_TASK_SPEC` mínimo con la persona que el orchestrator emite."""
    spec: dict[str, object] = {"agent_persona": {"prompt": prompt, "role": "backend_dev"}}
    spec.update(extra)
    return spec


def test_changing_the_agent_system_prompt_changes_the_label() -> None:
    """La propiedad ENTERA de `task_gov_03`, y la razón de ser de la tarea.

    Sin esto, `executions.prompt_version` es una etiqueta del andamiaje del
    runtime disfrazada de etiqueta del prompt: agrupa runs que corrieron con
    personas distintas y no puede atribuir un cambio de comportamiento a nada.
    """
    uno = prompt_version(agent_prompt_seal(_spec("Eres un backend senior de CI4.")))
    otro = prompt_version(agent_prompt_seal(_spec("Eres un QA meticuloso.")))
    assert uno != otro


def test_the_same_agent_prompt_gives_the_same_label() -> None:
    # La otra mitad: si la etiqueta se moviera con cada run del MISMO prompt, no
    # agruparía nada y el dashboard tendría una release por ejecución.
    texto = "Eres un arquitecto de software."
    assert prompt_version(agent_prompt_seal(_spec(texto))) == prompt_version(
        agent_prompt_seal(_spec(texto))
    )


def test_a_run_without_an_agent_prompt_keeps_the_historical_label() -> None:
    """Retro-compatibilidad, y no es cortesía: es lo que salva el histórico.

    Los runs ya etiquetados no llevan sello. Si `prompt_version()` sin sello
    devolviera otra cosa, el eje del dashboard se partiría en dos y la métrica que
    esta tarea arregla quedaría peor que antes de arreglarla.
    """
    assert agent_prompt_seal({}) is None
    assert prompt_version(None) == prompt_version()


def test_the_recorded_version_is_what_the_seal_names_when_it_travels() -> None:
    """Con `task_gov_02` desplegado, el sello nombra la FILA del historial.

    Es la diferencia entre «corrió con este texto» y «corrió con la versión 7, que
    firmó tal usuario tal día» — que es lo único que hace accionable la etiqueta
    cuando alguien pregunta «¿qué cambió?».
    """
    sello = agent_prompt_seal(
        _spec("Eres QA.", agent_prompt_version={"prompt_hash": "a" * 64, "version": 7})
    )
    assert sello == "v7:" + "a" * 64
    # Y la versión manda sobre el texto: dos runs del mismo texto en versiones
    # distintas no comparten etiqueta.
    otra = agent_prompt_seal(
        _spec("Eres QA.", agent_prompt_version={"prompt_hash": "a" * 64, "version": 8})
    )
    assert prompt_version(sello) != prompt_version(otra)


def test_the_agent_prompt_and_the_runtime_prompts_are_independent_axes() -> None:
    """Mover el andamiaje del runtime y mover la persona son cosas distintas.

    Comprobado sin tocar los módulos: dos personas distintas dan dos etiquetas
    distintas, y las dos difieren de la etiqueta sin persona. O sea que el sello se
    MEZCLA con el hash de los módulos y no lo sustituye — si lo sustituyera, un
    cambio en `nudges.py` dejaría de mover la etiqueta de los runs con persona,
    que es la mitad que `task_wf_52` ya había ganado.
    """
    base = prompt_version()
    uno = prompt_version(agent_prompt_seal(_spec("persona A")))
    otro = prompt_version(agent_prompt_seal(_spec("persona B")))
    assert len({base, uno, otro}) == 3


def test_the_label_keeps_its_shape_with_a_seal() -> None:
    # 12 hex: la columna, el filtro de la URL y el eje del dashboard no cambian.
    label = prompt_version(agent_prompt_seal(_spec("Eres QA.")))
    assert len(label) == 12
    assert all(c in "0123456789abcdef" for c in label)


def test_the_entrypoint_actually_passes_the_seal_to_run_agent() -> None:
    """El cableado, que es la mitad que esta base se suele dejar sin hacer.

    `verificar-antes-de-implementar.md` §5: el patrón dominante de este repo es
    «mecanismo entregado, cero llamantes». `agent_prompt_seal` podría estar
    perfecto y no llamarlo nadie, y todos los tests de arriba seguirían verdes
    mientras `executions.prompt_version` siguiera sin el prompt del agente.

    Se busca la INVOCACIÓN (`agent_seal=agent_prompt_seal(spec)`) y no una mención
    del nombre: el import y el comentario ya suman dos apariciones, así que contar
    apariciones daría verde con la llamada borrada.
    """
    from pathlib import Path

    entrypoint = Path(__file__).resolve().parents[1] / "agent_runtime" / "__main__.py"
    source = entrypoint.read_text(encoding="utf-8")
    assert "agent_seal=agent_prompt_seal(spec)" in source, (
        "el entrypoint ya no le pasa el sello a run_agent: la etiqueta volvería a"
        " hablar sólo del andamiaje del runtime"
    )

    graph = Path(__file__).resolve().parents[1] / "agent_runtime" / "graph.py"
    assert "prompt_version(agent_seal)" in graph.read_text(encoding="utf-8"), (
        "run_agent ya no mezcla el sello en prompt_version(): el argumento llegaría y se tiraría"
    )
