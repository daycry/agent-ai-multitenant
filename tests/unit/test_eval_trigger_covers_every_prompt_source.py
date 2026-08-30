"""El gate de evals se dispara con TODO lo que cambia un prompt efectivo.

## El defecto

`eval-on-prompt-change.yml` existe para que tocar el texto de un agente vuelva a
correr el harness de evals. Su filtro `paths:` nombraba dos ficheros
—`builtin_agents.py` y `qa_e2e_automator.py`— y se escribió cuando los prompts
vivían ahí. Desde entonces se repartieron:

* `ci4_team.py` define **diez** agentes con sus dos prompts;
* `tool_usage_guidance.py` no define ninguno: se **concatena** al prompt de
  todos, así que tocarla cambia el prompt efectivo de los 34 sin tocar ninguno
  de los dos ficheros vigilados.

Un filtro que no cubre la fuente no falla: **no se ejecuta**, que es la forma
cara de fallar — el gate sale verde en el listado de checks porque nunca corrió.

## La regla que fija este fichero

No se comprueba una lista contra otra lista escrita a mano —eso sólo mueve el
problema—, sino contra un hecho derivado: *un módulo de `seeds/` es fuente de
prompt si alguna constante de texto suya aparece dentro de algún prompt
efectivo*. Eso captura por igual al que declara agentes y al que sólo aporta un
párrafo, y no depende de cómo se llame el fichero ni de qué marcadores use.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.ci4_team import CI4_AGENTS
from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_SEEDS = _RAIZ / "apps" / "api-server" / "src" / "api_server" / "seeds"
_WORKFLOW = _RAIZ / ".github" / "workflows" / "eval-on-prompt-change.yml"

# Un fragmento corto ("Eres el", "\n\n") aparecería en cualquier prompt por
# casualidad y marcaría media carpeta como fuente. 60 caracteres de prosa
# continua no coinciden por azar.
_MIN_FRAGMENTO = 60


def _prompts_efectivos() -> tuple[str, ...]:
    out: list[str] = []
    for a in (*BUILTIN_AGENTS, QA_E2E_AUTOMATOR, *CI4_AGENTS):
        out.extend((a.effective_prompt_es, a.effective_prompt_en))
    return tuple(out)


def _constantes_de_texto(ruta: Path) -> list[str]:
    """Strings de módulo suficientemente largas para identificar su origen."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    fuera: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            texto = nodo.value
            if len(texto) >= _MIN_FRAGMENTO:
                fuera.append(texto)
    return fuera


def _fuentes_de_prompt() -> set[str]:
    prompts = _prompts_efectivos()
    fuentes: set[str] = set()
    for ruta in sorted(_SEEDS.glob("*.py")):
        if ruta.name.startswith("__"):
            continue
        for texto in _constantes_de_texto(ruta):
            if any(texto in prompt for prompt in prompts):
                fuentes.add(ruta.name)
                break
    return fuentes


def _paths_del_workflow() -> dict[str, list[str]]:
    datos = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML lee la clave `on:` como el booleano True (YAML 1.1).
    disparadores = datos.get("on", datos.get(True))
    return {
        evento: disparadores[evento]["paths"]
        for evento in ("push", "pull_request")
        if isinstance(disparadores.get(evento), dict) and "paths" in disparadores[evento]
    }


def test_hay_fuentes_de_prompt_que_detectar() -> None:
    """Si la detección devolviera vacío, los tests de abajo pasarían sin mirar.

    Es el modo de fallo silencioso de un test derivado: la derivación se rompe
    (un glob que no casa, un umbral demasiado alto) y el resultado es un
    conjunto vacío que satisface cualquier inclusión.
    """
    fuentes = _fuentes_de_prompt()
    assert len(fuentes) >= 3, (
        f"la detección de fuentes de prompt encontró {sorted(fuentes)}; se "
        "esperaban al menos builtin_agents.py, ci4_team.py y tool_usage_guidance.py"
    )


@pytest.mark.parametrize("evento", ["push", "pull_request"])
def test_el_disparador_cubre_toda_fuente_de_prompt(evento: str) -> None:
    paths = _paths_del_workflow()
    assert evento in paths, f"el workflow ya no filtra `paths` en {evento}"

    vigilados = {Path(p).name for p in paths[evento]}
    sin_vigilar = sorted(_fuentes_de_prompt() - vigilados)

    assert not sin_vigilar, (
        f"módulos de seeds/ cuyo texto acaba dentro de un prompt efectivo y que "
        f"NO disparan el gate de evals en `{evento}`: {sin_vigilar}. Tocarlos "
        "cambia el prompt sin que el harness llegue a correr, y el check sale "
        "verde por no haberse ejecutado."
    )


def test_las_dos_listas_de_paths_no_divergen() -> None:
    """`push` y `pull_request` vigilan lo mismo, o uno de los dos miente.

    Están duplicadas en el YAML (GitHub Actions no permite anclas entre
    disparadores), y dos listas copiadas a mano divergen: es como
    `ci4_team.py` acabó fuera de una sola de ellas en la primera versión de
    este arreglo.
    """
    paths = _paths_del_workflow()
    assert set(paths["push"]) == set(paths["pull_request"]), (
        "los filtros `paths` de push y pull_request difieren: "
        f"sólo en push {sorted(set(paths['push']) - set(paths['pull_request']))}, "
        f"sólo en PR {sorted(set(paths['pull_request']) - set(paths['push']))}"
    )
