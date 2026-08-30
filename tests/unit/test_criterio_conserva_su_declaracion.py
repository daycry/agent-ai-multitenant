"""La cuarta capa: un criterio estructurado deja de aplanarse por el camino.

**El defecto, medido.** El ADR 0162 encadena cuatro capas que impiden que exista
un criterio ejecutable, y la última es la que mata:
``_clean_acceptance_criteria`` estaba tipada ``-> list[str]`` y **aplanaba**
cualquier diccionario a su clave ``description``. Da igual lo que se reescriba
en los prompts o lo que declare el implementador: si el normalizador por el que
pasa TODO criterio tira el ``runtime``/``command``, no hay criterio ejecutable
que sobreviva a un replan, a una corrección o a una regeneración.

**Lo que este fichero fija, y sus dos mitades.** La primera —que la estructura
se conserve— es la que abre la opción A. La segunda es la que impide que la
primera rompa el producto: **la inmensa mayoría de los criterios son y seguirán
siendo prosa**, porque los dos generadores tienen escrito en el prompt que no
emitan comandos (y con razón: el planner planifica ANTES de que el código
exista). Un normalizador que empezara a devolver diccionarios donde antes había
cadenas se llevaría por delante todo lo que los renderiza.

**Y la tercera regla, la del ADR enunciada tres veces:** *un valor ausente no
puede significar nada más fuerte que «desconocido»*. Un criterio sin
``check_type`` es NO DECLARADO, no ``automated``; el normalizador no puede
inventarle una declaración que nadie escribió — es exactamente el vocabulario
del centinela ``_CHECK_TYPE_MISSING`` del worker, un piso más arriba.

Los tests se anclan en ``_normalise_plan_draft`` —el camino real por el que
baja el plan del PM— y en ``build_criteria_messages`` / ``format_sibling_context``
—los dos consumidores que reciben criterios ya normalizados—, no en el helper a
solas: un test sobre el helper sigue verde aunque nadie lo llame.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


_EJECUTABLE: dict[str, Any] = {
    "description": "los tests de Home pasan",
    "runtime": "php-phpunit",
    "command": "vendor/bin/phpunit --filter HomeTest",
    "check_type": "automated",
    "expected_signal": "exit_code == 0 and tests > 0",
    "timeout_s": 300,
}


def _criterios(criterios: list[Any]) -> list[Any]:
    """Los criterios de una tarea tal como salen del normalizador del planner."""
    from api_server.chat.planning_llm import _normalise_plan_draft

    out = _normalise_plan_draft(
        {"title": "x", "tasks": [{"id": "t1", "title": "T", "acceptance_criteria": criterios}]}
    )
    result: list[Any] = out["tasks"][0]["acceptance_criteria"]
    return result


# ---------------------------------------------------------------------------
# La mitad que abre la opción A: la estructura sobrevive
# ---------------------------------------------------------------------------


def test_un_criterio_ejecutable_sobrevive_al_normalizador() -> None:
    """El caso entero, en un test: entra un dict con `runtime` + `command` y sale
    un dict con `runtime` + `command`.

    Con el aplanado, salía la cadena «los tests de Home pasan» y el comando —el
    único dato que hace que la tarea se verifique de verdad— desaparecía sin un
    aviso."""
    salida = _criterios([_EJECUTABLE])

    assert isinstance(salida[0], dict), (
        "el normalizador aplanó un criterio ejecutable a su descripción: el "
        "comando se perdió y el test-runtime no se disparará"
    )
    assert salida[0]["runtime"] == "php-phpunit"
    assert salida[0]["command"] == "vendor/bin/phpunit --filter HomeTest"


def test_el_criterio_ejecutable_conserva_su_senal_y_su_presupuesto() -> None:
    """`expected_signal` y `timeout_s` viajan con el comando o no sirven de nada.

    El primero es la condición del §«La trampa que hay que cerrar CON A»: sin él
    un criterio puede salir verde habiendo ejecutado cero tests. El segundo es lo
    que evita que un check colgado bloquee la fase entera."""
    salida = _criterios([_EJECUTABLE])

    assert salida[0]["expected_signal"] == "exit_code == 0 and tests > 0"
    assert salida[0]["timeout_s"] == 300


def test_un_criterio_declarado_no_automatizable_conserva_su_motivo() -> None:
    """La otra mitad de la decisión de la opción A, y la que evita el gate ciego.

    «esto no es verificable a máquina, y este es el motivo» tiene que llegar
    entera: sin el motivo es indistinguible del silencio, que es justo lo que el
    ADR viene a retirar del juego. Una tarea de análisis o de documentación pasa
    honestamente por aquí."""
    manual = {
        "description": "el README explica la puesta en marcha",
        "check_type": "manual",
        "reason": "es prosa: no hay comando que compruebe que un texto se entiende",
    }
    salida = _criterios([manual])

    assert isinstance(salida[0], dict)
    assert salida[0]["check_type"] == "manual"
    assert salida[0]["reason"].startswith("es prosa")


# ---------------------------------------------------------------------------
# La mitad que no puede romperse: la prosa sigue siendo prosa
# ---------------------------------------------------------------------------


def test_la_prosa_sigue_saliendo_como_cadena() -> None:
    """NO-REGRESIÓN, y es el test que más importa del fichero.

    Los 25 criterios del plan real medido el 2026-08-29 son cadenas, y lo
    seguirán siendo: los dos generadores tienen prohibido por prompt emitir
    comandos. Si esto empezara a devolver diccionarios, todo lo que renderiza un
    criterio pasaría a pintar un objeto."""
    salida = _criterios(
        [
            "composer audit sin vulnerabilidades",
            "  composer.lock fija versiones  ",
            "",
            123,
            {"description": "PSR-4 correcto"},
        ]
    )

    assert salida == [
        "composer audit sin vulnerabilidades",
        "composer.lock fija versiones",
        "PSR-4 correcto",
    ]


def test_un_dict_a_medias_se_aplana_como_siempre() -> None:
    """Sin `runtime` Y `command`, y sin `check_type`, no hay nada estructurado que
    preservar: el worker exige los dos (`execution.py`), así que conservar la
    forma a medias fijaría un dato que no sirve para nada y ensuciaría el 100 %
    de los criterios de prosa que vienen envueltos."""
    assert _criterios([{"description": "algo", "runtime": "php-phpunit"}]) == ["algo"]
    assert _criterios([{"text": "otra cosa"}]) == ["otra cosa"]


def test_el_tope_de_criterios_sigue_valiendo_para_los_estructurados() -> None:
    """La estructura no compra una excepción a los límites: un plan no puede
    colar veinte comandos por la puerta de atrás."""
    muchos = [dict(_EJECUTABLE, description=f"c{i}") for i in range(20)]

    assert len(_criterios(muchos)) <= 8


# ---------------------------------------------------------------------------
# El silencio no se convierte en una declaración
# ---------------------------------------------------------------------------


def test_un_criterio_sin_check_type_no_se_inventa_uno() -> None:
    """*Un valor ausente no puede significar nada más fuerte que «desconocido»*.

    Si el normalizador rellenara `check_type: automated` estaría FABRICANDO la
    declaración que la opción A pide que alguien tome, y el contador de
    no-declarados del worker (`checks_without_declared_check_type`) pasaría a
    contar ceros para siempre."""
    sin_declarar = {
        "description": "los tests pasan",
        "runtime": "php-phpunit",
        "command": "vendor/bin/phpunit",
    }
    salida = _criterios([sin_declarar])

    assert isinstance(salida[0], dict)
    assert "check_type" not in salida[0], (
        "el normalizador inventó una declaración que nadie escribió: el silencio "
        "vuelve a leerse como «esto debería verificarse a máquina»"
    )


def test_una_clave_desconocida_no_viaja_a_la_base_de_datos() -> None:
    """Conservar la estructura no es conservar cualquier cosa: el conjunto de
    claves es CERRADO. El criterio acaba en un JSONB y en el prompt del agente;
    dejar pasar lo que sea es la vía por la que `repository_config` acumuló siete
    claves que nadie lee (§Decisión 1 del ADR)."""
    salida = _criterios([dict(_EJECUTABLE, cualquier_cosa="ruido")])

    assert isinstance(salida[0], dict)
    assert "cualquier_cosa" not in salida[0]


# ---------------------------------------------------------------------------
# Los consumidores siguen leyendo texto
# ---------------------------------------------------------------------------


def test_el_generador_de_criterios_lee_el_texto_de_un_criterio_estructurado() -> None:
    """`build_criteria_messages` recibe los criterios ACTUALES de la tarea para
    refinarlos. Si un criterio ejecutable llegara ahí como `repr` de un dict, el
    modelo leería `{'description': ...}` y lo refinaría como si fuera texto."""
    from api_server.chat.criteria_llm import build_criteria_messages

    mensajes = build_criteria_messages(
        title="T",
        description=None,
        existing=[_EJECUTABLE, "un criterio de prosa"],
        project_context={},
    )
    user = mensajes[-1].content

    assert "los tests de Home pasan" in user
    assert "un criterio de prosa" in user
    assert "{'description'" not in user and '{"description"' not in user


def test_el_contexto_de_hermanas_lee_el_texto_de_un_criterio_estructurado() -> None:
    from api_server.chat.criteria_llm import format_sibling_context

    texto = format_sibling_context([("Otra tarea", [_EJECUTABLE])])

    assert "los tests de Home pasan" in texto
    assert "{'description'" not in texto


def test_la_generacion_por_ia_sigue_devolviendo_prosa() -> None:
    """El endpoint «Generar con IA» es una propuesta que un OPERADOR revisa en la
    UI, y su contrato es prosa (`GeneratedAcceptanceCriteria.acceptance_criteria:
    list[str]`). La estructura no entra por esa puerta: según la reformulación de
    la opción A, quien declara el comando es quien acaba de escribir el test, no
    un generador que trabaja antes de que el código exista."""
    from api_server.chat.criteria_llm import _extract_criteria

    salida = _extract_criteria(
        '{"acceptance_criteria": [{"description": "los tests pasan", '
        '"runtime": "php-phpunit", "command": "vendor/bin/phpunit"}, "prosa"]}'
    )

    assert salida == ["los tests pasan", "prosa"]
    assert all(isinstance(c, str) for c in salida)


def test_el_recall_automatico_lee_el_texto_de_un_criterio_estructurado() -> None:
    """El boot enriquece la query del recall con los primeros criterios de la
    tarea. Con ``str(c)`` sobre un criterio estructurado, lo que se busca en la
    memoria del tenant es ``{'description': …, 'command': …}``: media query es el
    ``repr`` de un diccionario, y el recall recupera peor justo en las tareas que
    SÍ declaran cómo se verifican.

    Era un defecto latente —el operador ya podía escribir criterios estructurados
    desde la UI— que el normalizador nuevo hace más probable, así que se cierra
    aquí y no se deja para que lo encuentre otro."""
    from agent_runtime.__main__ import _build_auto_recall

    visto: dict[str, str] = {}

    class _ApiFalsa:
        def memory_recall(self, *, query: str, limit: int) -> list[Any]:
            visto["query"] = query
            return []

    recall = _build_auto_recall(_ApiFalsa(), role="backend_dev")
    assert recall is not None
    recall({"title": "Home", "description": "una home", "acceptance_criteria": [_EJECUTABLE]})

    assert "los tests de Home pasan" in visto["query"]
    assert "{'description'" not in visto["query"]
