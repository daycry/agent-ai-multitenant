"""`docs/context/architecture-overview.md` cubre TODOS los subsistemas + diagramas.

Acredita el ítem 1 del test humano `human_doc_01` del plan
`docs-comprehensive-update` («architecture-overview describe todos los subsistemas
—incl. human agents, providers, marketplace, guardrails, budgets, webhooks,
evals— con diagramas Mermaid que renderizan»). Precedente de forma:
`tests/docs/test_docs_training_model.py`.

## De dónde sale la matriz

De los dos sitios que la fijan, no de intuición:

  * el **checklist del test humano**, que nombra siete subsistemas explícitos;
  * el **§Alcance del plan**, que enumera lo que ese documento debía ganar:
    «Human Agent, HumanWorkSession, review modes, llm_providers,
    allowed_commands, runtime templates, marketplace listing/trust, budget/FX,
    guardrails, webhooks, evals…» + «topología de contenedores».

Cada entrada de la matriz es (etiqueta, alternativas aceptadas): se busca
insensible a mayúsculas y se admite más de una grafía cuando el término tiene
nombre de código y nombre de producto (p. ej. `llm_providers` / «proveedores
LLM»). Un fallo nombra el subsistema ausente, no un `assert False` opaco.

## Sobre «que renderizan»

Renderizar Mermaid pide un navegador; un test estático no puede afirmarlo. Lo que
sí se puede afirmar —y es donde estaba el riesgo real— es lo estructural: que los
bloques existen (el plan prometía tres: componentes, flujo de un plan,
multi-tenancy), que cada uno **abre con un tipo de diagrama conocido** (un bloque
```mermaid vacío o con prosa dentro es exactamente lo que no renderiza) y que las
vallas están balanceadas. Que el dibujo sea legible sigue siendo del humano.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERVIEW = _REPO_ROOT / "docs" / "context" / "architecture-overview.md"

#: (subsistema, grafías aceptadas). Del checklist de human_doc_01 + §Alcance.
_SUBSYSTEMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("human agents", ("human agent", "agente humano", "human_agent")),
    ("HumanWorkSession", ("humanworksession", "human_work_session")),
    # El documento lo nombra por la columna real (`human_task_review_mode`), que
    # es lo que hay que buscar: «review modes» es el término del plan, no del código.
    (
        "review modes",
        ("human_task_review_mode", "review mode", "peer_human_reviewer"),
    ),
    ("proveedores LLM", ("llm_providers", "proveedores llm", "llm provider")),
    ("catálogo cerrado de providers", ("claude", "copilot", "azure", "ollama")),
    ("marketplace", ("marketplace",)),
    ("confianza del marketplace", ("trust", "confianza")),
    ("guardrails", ("guardrail",)),
    ("budgets", ("budget", "presupuesto")),
    ("FX / coste", ("fx", "usd")),
    ("webhooks", ("webhook",)),
    ("evals", ("eval",)),
    ("allowed_commands", ("allowed_commands", "comandos permitidos")),
    ("runtime templates", ("runtime template", "runtime_template")),
    ("multi-tenancy / RLS", ("rls",)),
    ("memoria / RAG", ("rag", "memoria")),
    ("MCP", ("mcp",)),
    ("worktrees git", ("worktree",)),
    ("topología de contenedores", ("docker compose", "contenedor")),
    ("sandbox / aislamiento", ("seccomp", "sandbox")),
)

#: Tipos de diagrama Mermaid que la doc del repo usa (convención `conventions.md`).
_MERMAID_TYPES = (
    "flowchart",
    "graph",
    "sequencediagram",
    "erdiagram",
    "classdiagram",
    "statediagram",
    "gantt",
    "journey",
    "mindmap",
    "timeline",
)

#: El plan prometía tres diagramas (componentes + flujo de un plan + multi-tenancy).
_MIN_MERMAID_BLOCKS = 3


def _text() -> str:
    return _OVERVIEW.read_text(encoding="utf-8")


def _mermaid_blocks(text: str) -> list[str]:
    return re.findall(r"^```mermaid\s*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


# --- existencia y volumen ---------------------------------------------------


def test_architecture_overview_exists_and_is_substantial() -> None:
    assert _OVERVIEW.is_file(), "falta docs/context/architecture-overview.md"
    text = _text()
    assert len(text) > 5000, (
        f"architecture-overview.md tiene {len(text)} caracteres: no puede describir "
        "el sistema final end-to-end (el plan lo reescribió entero)"
    )


# --- la matriz de subsistemas ----------------------------------------------


@pytest.mark.parametrize(("subsystem", "spellings"), _SUBSYSTEMS, ids=[s for s, _ in _SUBSYSTEMS])
def test_architecture_overview_covers_subsystem(subsystem: str, spellings: tuple[str, ...]) -> None:
    lowered = _text().lower()
    assert any(sp in lowered for sp in spellings), (
        f"architecture-overview.md no menciona el subsistema {subsystem!r} "
        f"(ninguna de {list(spellings)}): el test humano human_doc_01 exige que "
        "el documento describa TODOS los subsistemas del sistema final"
    )


# --- diagramas Mermaid ------------------------------------------------------


def test_architecture_overview_has_the_promised_mermaid_diagrams() -> None:
    blocks = _mermaid_blocks(_text())
    assert len(blocks) >= _MIN_MERMAID_BLOCKS, (
        f"architecture-overview.md tiene {len(blocks)} bloques Mermaid; el plan "
        f"prometía al menos {_MIN_MERMAID_BLOCKS} (componentes + flujo de un plan "
        "+ multi-tenancy)"
    )


def test_every_mermaid_block_declares_a_known_diagram_type() -> None:
    """Un bloque ```mermaid vacío o con prosa dentro es lo que NO renderiza."""
    blocks = _mermaid_blocks(_text())
    assert blocks, "no se encontró ningún bloque Mermaid (¿cambió el vallado?)"
    offenders: list[str] = []
    for i, block in enumerate(blocks, start=1):
        first = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")
        if not first.lower().startswith(_MERMAID_TYPES):
            offenders.append(f"bloque #{i}: primera línea {first!r}")
    assert not offenders, (
        "bloques Mermaid que no abren con un tipo de diagrama conocido (no "
        f"renderizarían): {offenders}"
    )


def test_no_mermaid_block_swallowed_the_document() -> None:
    """Una valla ```mermaid sin cerrar se traga la prosa que sigue.

    Contar aperturas contra cierres NO lo detecta: si falta el cierre, el bloque
    se cierra en la siguiente valla que haya en el documento y las cuentas
    cuadran. El síntoma que sí se ve es el contenido: un diagrama no lleva
    dentro encabezados Markdown ni otra apertura de valla. Este assert nació de
    un ciclo red-green donde la versión «balanceada» se quedó verde con la valla
    de cierre borrada a mano.
    """
    text = _text()
    opens = len(re.findall(r"^```mermaid\s*$", text, re.MULTILINE))
    blocks = _mermaid_blocks(text)
    assert opens == len(blocks), (
        f"{opens} aperturas ```mermaid pero {len(blocks)} bloques cerrados: "
        "hay una valla sin cerrar al final del documento"
    )
    offenders: list[str] = []
    for i, block in enumerate(blocks, start=1):
        if re.search(r"^#{1,6}\s+\S", block, re.MULTILINE):
            offenders.append(f"bloque #{i}: contiene un encabezado Markdown")
        elif "```" in block:
            offenders.append(f"bloque #{i}: contiene otra apertura de valla")
    assert not offenders, (
        "bloque(s) Mermaid que se tragaron prosa del documento (falta su valla "
        f"de cierre): {offenders}"
    )
