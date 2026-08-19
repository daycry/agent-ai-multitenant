"""El sello del prompt del agente cruza la frontera de imágenes sin cambiar de valor.

`task_gov_03`. `executions.prompt_version` hasheaba `providers`, `nudges` y
`review_contract` — el andamiaje del runtime— y **ni un byte del `system_prompt`
del agente**. Dos runs con el mismo `prompt_version` podían haber corrido con
personas completamente distintas, así que la etiqueta que existe para atribuir un
cambio de comportamiento no podía atribuir nada.

## Por qué este fichero existe, y por qué es un test y no un comentario

El sello se calcula en DOS sitios que no se pueden importar el uno al otro:

* la **api-server** (`agent_persona.prompt_text_hash`), que se lo manda al worker
  en el spec del run;
* el **agent-runtime** (`prompt_version.agent_prompt_seal`), que lo recalcula del
  texto de la persona cuando el spec no lo trae — el caso de un agente que nunca
  se editó, o sea el mayoritario el primer día.

Viven en imágenes Docker distintas (`docker/agent-runtimes/agent-runtime/`), y el
runtime no puede depender de `api_server`. O sea que son dos implementaciones del
mismo contrato, la forma clásica de que dos mitades se desincronicen sin que nada
falle: el mismo prompt produciría dos etiquetas según por qué rama entró, y el
dashboard mostraría dos releases donde hay una.

Este fichero es el único sitio del repo donde las dos se comparan.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from api_server.agent_persona import (
    PERSONA_MAX_CHARS,
    effective_prompt_hash,
    prompt_text_hash,
    resolve_agent_persona,
)

pytestmark = pytest.mark.unit


def _load_runtime_prompt_version() -> ModuleType:
    """`agent_runtime.prompt_version`, cargado de su propio árbol.

    El agent-runtime NO es un paquete instalado en este venv: vive en
    `docker/agent-runtimes/agent-runtime/` porque se empaqueta en otra imagen. Hay
    que ponerlo en el path a mano, y esa fricción es precisamente lo que hace que
    las dos mitades del sello se desincronicen sin que nadie se entere.

    Se carga con `importlib` y no con un `from … import` tras el `sys.path.insert`
    para que no haga falta silenciar el import fuera de cabecera: un import
    condicional a medio fichero es exactamente lo que la regla de estilo señala, y
    aquí hay una forma de escribirlo que no lo necesita.
    """
    root = Path(__file__).resolve().parents[2] / "docker" / "agent-runtimes" / "agent-runtime"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("agent_runtime.prompt_version")


_RUNTIME = _load_runtime_prompt_version()
agent_prompt_seal = _RUNTIME.agent_prompt_seal
prompt_version = _RUNTIME.prompt_version


def _agent(*, system_prompt: str = "", system_prompts: dict[str, str] | None = None):
    model_config: dict[str, object] = {"provider": "claude_sdk", "model": "sonnet"}
    if system_prompts is not None:
        model_config["system_prompts"] = system_prompts
    return SimpleNamespace(
        system_prompt=system_prompt,
        model_config=model_config,
        role="backend_dev",
        name="Backend Senior",
    )


def _spec_for(agent) -> dict[str, object]:
    """El spec que el orchestrator emite para ``agent``, sin la fila de historial.

    Reproduce la rama de `dispatch._assemble_run_request` en la que el agente
    todavía no tiene versión registrada: `agent_persona` presente, y del
    `agent_prompt_version` sólo el hash.
    """
    persona = resolve_agent_persona(agent)
    spec: dict[str, object] = {}
    if persona is not None:
        spec["agent_persona"] = persona
    return spec


# ---------------------------------------------------------------------------
# El contrato: las dos implementaciones dan el MISMO número
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "agent",
    [
        _agent(system_prompt="Eres un backend senior de CodeIgniter 4."),
        _agent(system_prompts={"es": "Eres QA.", "en": "You are QA."}),
        _agent(system_prompt="plano", system_prompts={"en": "Only English here."}),
        # Acentos y emoji: el encode('utf-8') tiene que ser el mismo a los dos
        # lados, y un `encode('latin-1')` en cualquiera de ellos se vería aquí.
        _agent(system_prompt="Revisión de código — ojo con los añadidos ✅"),
    ],
)
def test_both_sides_hash_the_same_text_to_the_same_digest(agent) -> None:
    del_servidor = effective_prompt_hash(agent)
    del_runtime = agent_prompt_seal(_spec_for(agent))
    assert del_runtime == f"p:{del_servidor}", (
        "el hash de la api-server y el del agent-runtime han divergido: el mismo"
        " prompt produciría dos etiquetas según por qué rama entró"
    )


def test_the_runtime_hash_is_a_plain_sha256_of_the_effective_text() -> None:
    """Fija la FÓRMULA, no sólo que las dos coincidan.

    Sin esto, cambiar las dos mitades a la vez (a un md5, a un sha256 con sal)
    dejaría el test de arriba en verde mientras se rompe todo el histórico ya
    etiquetado.
    """
    agent = _agent(system_prompt="Eres un agente de implementación.")
    esperado = hashlib.sha256("Eres un agente de implementación.".encode()).hexdigest()
    assert effective_prompt_hash(agent) == esperado
    assert agent_prompt_seal(_spec_for(agent)) == f"p:{esperado}"


def test_what_is_sealed_is_the_EFFECTIVE_text_not_the_flat_column() -> None:
    """Dos agentes con el mismo campo plano y distinto `system_prompts.es`.

    Sellar `agents.system_prompt` en vez del texto efectivo los haría idénticos, y
    son lo contrario: al modelo le llega el bilingüe, así que corren con personas
    distintas.
    """
    uno = _agent(system_prompt="mismo plano", system_prompts={"es": "Eres arquitecto."})
    otro = _agent(system_prompt="mismo plano", system_prompts={"es": "Eres QA."})
    assert uno.system_prompt == otro.system_prompt
    assert effective_prompt_hash(uno) != effective_prompt_hash(otro)


def test_a_prompt_longer_than_the_cap_is_sealed_AS_TRUNCATED() -> None:
    """El cap de la persona forma parte del contrato del sello.

    `resolve_agent_persona` capa a `PERSONA_MAX_CHARS`, así que dos prompts que
    sólo difieren pasados ese punto llegan IDÉNTICOS al modelo. El sello tiene que
    decir lo mismo: si hasheara el texto completo, afirmaría una diferencia que el
    modelo no vio, y `agent_prompt_versions` ya guarda el crudo para el diff.
    """
    base = "x" * PERSONA_MAX_CHARS
    uno = _agent(system_prompt=base + "cola A")
    otro = _agent(system_prompt=base + "cola B distinta y más larga")
    assert effective_prompt_hash(uno) == effective_prompt_hash(otro)
    # Y el agent-runtime, que hashea lo que le llega en el spec, coincide: la
    # persona que viaja ya viene capada por el mismo resolutor.
    assert agent_prompt_seal(_spec_for(uno)) == agent_prompt_seal(_spec_for(otro))


# ---------------------------------------------------------------------------
# Las dos formas del sello, y la ausencia de sello
# ---------------------------------------------------------------------------
def test_the_recorded_version_wins_over_the_raw_text() -> None:
    """Con fila de historial, el sello nombra la VERSIÓN, no sólo el contenido.

    Es la diferencia entre «este run corrió con este texto» y «este run corrió con
    la versión 4, que escribió tal usuario tal día».
    """
    agent = _agent(system_prompt="Eres QA.")
    spec = _spec_for(agent)
    spec["agent_prompt_version"] = {"prompt_hash": effective_prompt_hash(agent), "version": 4}
    assert agent_prompt_seal(spec) == f"v4:{effective_prompt_hash(agent)}"


def test_a_version_of_None_falls_back_to_the_unversioned_shape() -> None:
    """El agente que nunca se editó: hash sí, número no.

    El dispatch manda `version: None` a propósito en ese caso, en vez de omitir la
    clave: así el runtime recibe el hash autoritativo del servidor igualmente.
    """
    agent = _agent(system_prompt="Eres QA.")
    spec = _spec_for(agent)
    spec["agent_prompt_version"] = {"prompt_hash": effective_prompt_hash(agent), "version": None}
    assert agent_prompt_seal(spec) == f"p:{effective_prompt_hash(agent)}"


def test_a_boolean_version_is_not_a_version() -> None:
    """`bool` es subclase de `int` en Python, y ahí hay una trampa real.

    Sin descartarlo, un `version: true` de un spec mal formado sellaría el run como
    «versión 1» y lo ataría a una fila del historial que no es la suya.
    """
    agent = _agent(system_prompt="Eres QA.")
    spec = _spec_for(agent)
    spec["agent_prompt_version"] = {"prompt_hash": effective_prompt_hash(agent), "version": True}
    assert agent_prompt_seal(spec) == f"p:{effective_prompt_hash(agent)}"


def test_an_agent_without_persona_gets_no_seal() -> None:
    """Sin persona no hay nada que sellar, y el hash del vacío no es un sello.

    Emitirlo movería la etiqueta de TODOS esos runs sin distinguir nada entre
    ellos, que es peor que no tocarla: rompe la comparabilidad con el histórico a
    cambio de cero información.
    """
    assert agent_prompt_seal({}) is None
    assert agent_prompt_seal({"agent_persona": {"prompt": "   "}}) is None
    assert agent_prompt_seal({"agent_prompt_version": {"prompt_hash": "", "version": 2}}) is None


def test_no_seal_reproduces_the_historical_label_byte_for_byte() -> None:
    """La etiqueta de antes de `task_gov_03` no se mueve.

    Es lo que mantiene comparables los runs ya etiquetados: si `prompt_version()`
    sin sello devolviera otra cosa, el eje del dashboard se partiría en dos y la
    métrica que esta tarea existe para arreglar quedaría peor que antes.
    """
    assert prompt_version(None) == prompt_version()
    assert prompt_version("") == prompt_version()


def test_the_seal_actually_moves_the_label() -> None:
    """La propiedad entera de `task_gov_03`, en una línea."""
    uno = _agent(system_prompt="Eres un backend senior.")
    otro = _agent(system_prompt="Eres un QA meticuloso.")
    sello_uno = agent_prompt_seal(_spec_for(uno))
    sello_otro = agent_prompt_seal(_spec_for(otro))
    assert sello_uno != sello_otro
    assert prompt_version(sello_uno) != prompt_version(sello_otro)
    # Y ninguna de las dos coincide con la etiqueta sin sello, que es la que
    # compartían ANTES de esta tarea.
    assert prompt_version(sello_uno) != prompt_version()
    assert prompt_version(sello_otro) != prompt_version()


def test_the_versioned_and_unversioned_seals_of_the_same_text_differ() -> None:
    """«La versión 3» y «este texto» son dos afirmaciones distintas.

    Colapsarlas en la misma etiqueta perdería la única cosa que la fila de
    historial añade sobre el contenido: que alguien la firmó.
    """
    agent = _agent(system_prompt="Eres QA.")
    sin_version = agent_prompt_seal(_spec_for(agent))
    spec = _spec_for(agent)
    spec["agent_prompt_version"] = {"prompt_hash": effective_prompt_hash(agent), "version": 3}
    con_version = agent_prompt_seal(spec)
    assert sin_version != con_version
    assert prompt_version(sin_version) != prompt_version(con_version)


def test_the_label_is_still_short_and_hex_with_a_seal() -> None:
    # La columna, el filtro de la URL y el eje del dashboard siguen siendo los
    # mismos: el sello no puede alargar la etiqueta.
    label = prompt_version(agent_prompt_seal(_spec_for(_agent(system_prompt="Eres QA."))))
    assert len(label) == 12
    assert all(c in "0123456789abcdef" for c in label)


def test_prompt_text_hash_is_the_shared_primitive() -> None:
    # `effective_prompt_hash` tiene que ser exactamente `prompt_text_hash` del
    # texto efectivo: si se bifurcaran, el sello del servidor y el del runtime
    # dejarían de venir de la misma fórmula.
    agent = _agent(system_prompts={"es": "Eres arquitecto."})
    assert effective_prompt_hash(agent) == prompt_text_hash("Eres arquitecto.")
