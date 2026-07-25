"""Unit tests for the LLM-backed planning model adapter (planning chat wiring)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from api_server.chat.planning_graph import (
    PlanningRole,
    PlanningState,
    PMIntent,
    SpecialistContribution,
)
from api_server.chat.planning_llm import (
    LLMPlanningModel,
    _normalise_plan_draft,
    _suggest_specialists,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk


@dataclass
class _FakeProvider:
    """Returns a fixed `content` for every complete() call."""

    content: str
    name: str = "fake"
    seen: list[list[Message]] | None = None

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        if self.seen is not None:
            self.seen.append(list(messages))
        return CompletionResponse(content=self.content, model=model or "m", provider=self.name)

    async def stream(self, *a: Any, **k: Any) -> AsyncIterator[StreamChunk]:  # pragma: no cover
        yield StreamChunk(delta="", done=True)

    async def aclose(self) -> None:  # pragma: no cover
        return None


def _state(history: list[dict[str, Any]], roles: set[PlanningRole]) -> PlanningState:
    return PlanningState(chat_history=history, team_roles=frozenset(roles))


def test_suggest_specialists_detects_disciplines_intersected_with_team() -> None:
    available = frozenset({PlanningRole.BACKEND_DEV, PlanningRole.SECURITY, PlanningRole.QA})
    got = set(_suggest_specialists("API con base de datos, auth con JWT y tests", available))
    assert got == {PlanningRole.BACKEND_DEV, PlanningRole.SECURITY, PlanningRole.QA}
    # A discipline the team doesn't have is NOT returned even if mentioned.
    assert _suggest_specialists("arquitectura multi-tenant", available) == ()


def test_pm_decide_nudges_invite_for_multidisciplinary_request() -> None:
    # The model answers alone, but the request clearly spans several disciplines →
    # the deterministic nudge convenes the matching specialists.
    provider = _FakeProvider('{"intent": "speak_alone", "rationale": "lo hago yo"}')
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    state = _state(
        [
            {
                "role": "user",
                "content": (
                    "API multi-tenant con base de datos Doctrine, auth/login con roles, "
                    "panel frontend y tests"
                ),
            }
        ],
        {
            PlanningRole.PROJECT_MANAGER,
            PlanningRole.ARCHITECT,
            PlanningRole.BACKEND_DEV,
            PlanningRole.SECURITY,
            PlanningRole.FRONTEND_DEV,
            PlanningRole.QA,
        },
    )
    directive = model.pm_decide(state)
    assert directive.intent == PMIntent.INVITE_SPECIALISTS
    assert PlanningRole.BACKEND_DEV in directive.specialists
    assert PlanningRole.SECURITY in directive.specialists
    assert PlanningRole.ARCHITECT in directive.specialists


def test_pm_decide_keeps_speak_alone_for_trivial_request() -> None:
    provider = _FakeProvider('{"intent": "speak_alone", "rationale": "ajuste menor"}')
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    state = _state(
        [{"role": "user", "content": "cambia el texto del botón de enviar"}],
        {PlanningRole.PROJECT_MANAGER, PlanningRole.FRONTEND_DEV},
    )
    directive = model.pm_decide(state)
    assert directive.intent == PMIntent.SPEAK_ALONE


def test_default_output_budget_is_generous_enough_for_a_full_plan() -> None:
    # Regression: max_tokens=1024 truncated a 6-phase plan mid-sentence. The
    # synthesis (a full structured ## Plan) needs a much larger output budget.
    model = LLMPlanningModel(provider=_FakeProvider("## Plan"))  # type: ignore[arg-type]
    assert model.max_tokens >= 4096


def test_pm_decide_parses_json_intent_and_specialists() -> None:
    provider = _FakeProvider(
        '{"intent": "invite_specialists", "rationale": "necesito arquitectura", '
        '"specialists": ["architect", "qa", "backend_dev"]}'
    )
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    state = _state(
        [{"role": "user", "content": "Quiero una app CI4"}],
        {PlanningRole.PROJECT_MANAGER, PlanningRole.ARCHITECT, PlanningRole.QA},
    )
    directive = model.pm_decide(state)
    assert directive.intent == PMIntent.INVITE_SPECIALISTS
    # backend_dev is not in the team → graph filters it later; here we keep what
    # the model said, but only valid PlanningRoles are parsed.
    assert PlanningRole.ARCHITECT in directive.specialists
    assert PlanningRole.QA in directive.specialists


def test_pm_decide_unknown_intent_defaults_to_speak_alone() -> None:
    provider = _FakeProvider('{"intent": "garbage", "rationale": "x"}')
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    directive = model.pm_decide(_state([], {PlanningRole.PROJECT_MANAGER}))
    assert directive.intent == PMIntent.SPEAK_ALONE
    assert directive.specialists == ()


def test_pm_decide_extracts_json_embedded_in_prose() -> None:
    provider = _FakeProvider('Claro:\n{"intent": "ask_user", "rationale": "faltan datos"}\nGracias')
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    directive = model.pm_decide(_state([], {PlanningRole.PROJECT_MANAGER}))
    assert directive.intent == PMIntent.ASK_USER


def test_specialist_speak_returns_role_content() -> None:
    provider = _FakeProvider("Propongo separar dominio y persistencia.")
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    contrib = model.specialist_speak(PlanningRole.ARCHITECT, _state([], {PlanningRole.ARCHITECT}))
    assert contrib.role == PlanningRole.ARCHITECT
    assert "dominio" in contrib.content


def test_pm_synthesise_includes_contributions_and_returns_text() -> None:
    seen: list[list[Message]] = []
    provider = _FakeProvider("Resumen del equipo: …", seen=seen)
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    contributions = [
        SpecialistContribution(role=PlanningRole.ARCHITECT, content="capas limpias"),
        SpecialistContribution(role=PlanningRole.QA, content="tests e2e"),
    ]
    out = model.pm_synthesise(_state([{"role": "user", "content": "app"}], set()), contributions)
    assert out == "Resumen del equipo: …"
    # The specialist contributions were folded into the prompt.
    joined = " ".join(m.content for m in seen[-1])
    assert "capas limpias" in joined
    assert "tests e2e" in joined


def test_specialist_speak_enforces_shared_friendly_template() -> None:
    # Regression: specialists rendered in wildly different shapes (one "Objetivo:",
    # another "PLAN DE IMPLEMENTACIÓN"). Every specialist must follow ONE skeleton.
    seen: list[list[Message]] = []
    provider = _FakeProvider("ok", seen=seen)
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    model.specialist_speak(
        PlanningRole.BACKEND_DEV,
        _state([{"role": "user", "content": "x"}], {PlanningRole.BACKEND_DEV}),
    )
    system = " ".join(m.content for m in seen[-1] if m.role == "system")
    assert "FORMATO OBLIGATORIO" in system
    assert "**Objetivo:**" in system
    assert "**Tareas propuestas:**" in system
    assert "criterio de aceptación" in system


def test_synthesis_enforces_friendly_plan_template() -> None:
    # The synthesis must visibly follow the friendly plan layout, not free prose.
    seen: list[list[Message]] = []
    provider = _FakeProvider("## Plan", seen=seen)
    model = LLMPlanningModel(provider=provider)  # type: ignore[arg-type]
    model.pm_synthesise(_state([{"role": "user", "content": "app"}], set()), [])
    system = " ".join(m.content for m in seen[-1] if m.role == "system")
    assert "FORMATO OBLIGATORIO" in system
    assert "## Plan" in system
    assert "### Fase" in system


def test_specialist_and_synthesis_share_task_line_convention() -> None:
    # The SAME task-line convention drives specialist contributions and the final
    # plan, so the whole conversation renders with one consistent, scannable look.
    seen_c: list[list[Message]] = []
    seen_s: list[list[Message]] = []
    LLMPlanningModel(provider=_FakeProvider("c", seen=seen_c)).specialist_speak(  # type: ignore[arg-type]
        PlanningRole.QA, _state([{"role": "user", "content": "x"}], {PlanningRole.QA})
    )
    LLMPlanningModel(provider=_FakeProvider("s", seen=seen_s)).pm_synthesise(  # type: ignore[arg-type]
        _state([{"role": "user", "content": "x"}], set()), []
    )
    contrib_sys = " ".join(m.content for m in seen_c[-1] if m.role == "system")
    synth_sys = " ".join(m.content for m in seen_s[-1] if m.role == "system")
    for marker in ("_depende de_", "_criterio de aceptación_"):
        assert marker in contrib_sys
        assert marker in synth_sys


def test_normalise_plan_draft_fills_ids_and_drops_bad_deps() -> None:
    out = _normalise_plan_draft(
        {
            "title": "Landing CI4",
            "summary": "Sin BD",
            "tasks": [
                {"title": "Controlador Home", "depends_on": []},  # no id → t1
                {"id": "t2", "title": "Vista Twig", "depends_on": ["t1", "ghost", "t2"]},
                {"id": "t3", "name": "POST saludar", "description": "echo nombre"},  # name→title
                {"id": "t4"},  # no title → dropped
                "garbage",  # non-dict → dropped
            ],
        }
    )
    assert out["title"] == "Landing CI4"
    ids = [t["id"] for t in out["tasks"]]
    assert ids == ["t1", "t2", "t3"]  # t4 (no title) + garbage dropped
    t2 = next(t for t in out["tasks"] if t["id"] == "t2")
    assert t2["depends_on"] == ["t1"]  # ghost (unknown) + self ref dropped
    assert out["tasks"][2]["title"] == "POST saludar"  # name → title


def test_normalise_plan_draft_summary_is_an_object_not_a_string() -> None:
    """A-03: `PlanSpecification.summary` es un `dict`, no un `str`.

    El draft del chat lo emitía como cadena y `create_plan` lo persiste SIN pasar
    por Pydantic, así que el 422 aparecía después, en cualquier `PUT` que
    reenviara el spec. Y la UI hacía `Object.keys("texto")` → `["0","1",…]`,
    concluía que había resumen y pintaba una tarjeta vacía (busca
    `summary.description`, que en una cadena no existe)."""
    out = _normalise_plan_draft(
        {"title": "X", "summary": "Sin BD", "tasks": [{"id": "t1", "title": "A"}]}
    )
    assert isinstance(out["summary"], dict)
    assert out["summary"]["description"] == "Sin BD"


def test_normalise_plan_draft_accepts_a_structured_summary() -> None:
    """Si el modelo YA emite el objeto (con alcance), se respeta tal cual."""
    rich = {"description": "API de inventario", "scope_in": ["CRUD"], "scope_out": ["pagos"]}
    out = _normalise_plan_draft(
        {"title": "X", "summary": rich, "tasks": [{"id": "t1", "title": "A"}]}
    )
    assert out["summary"] == rich


def test_normalise_plan_draft_derives_hours_from_complexity() -> None:
    """A-04: sin `estimated_hours` el Gantt pintaba barras IDÉNTICAS y el coste
    humano era `nº_tareas × 4 h × tarifa` — un número con aspecto de dato y sin
    información. El planner emite `complexity`, así que las horas se derivan de
    ahí; si el modelo da un valor explícito, ese gana."""
    out = _normalise_plan_draft(
        {
            "title": "X",
            "tasks": [
                {"id": "t1", "title": "Diminuta", "complexity": "xs"},
                {"id": "t2", "title": "Enorme", "complexity": "xl"},
                {"id": "t3", "title": "Con horas propias", "complexity": "s", "estimated_hours": 9},
            ],
        }
    )
    by_id = {t["id"]: t for t in out["tasks"]}
    assert by_id["t1"]["estimated_hours"] < by_id["t2"]["estimated_hours"]
    assert by_id["t3"]["estimated_hours"] == 9.0  # el explícito manda
    assert all(t["estimated_hours"] > 0 for t in out["tasks"])


def test_normalise_plan_draft_empty_when_no_tasks() -> None:
    out = _normalise_plan_draft({"title": "x"})
    assert out["tasks"] == []
    assert out["title"] == "x"


def test_normalise_plan_draft_extracts_acceptance_criteria() -> None:
    # The planner emits per-task acceptance_criteria as descriptive, verifiable
    # strings (the agent's definition of done). The parser cleans them: trims,
    # drops empties/non-strings, flattens {description} dicts.
    out = _normalise_plan_draft(
        {
            "title": "x",
            "tasks": [
                {
                    "id": "t1",
                    "title": "Auditar deps",
                    "acceptance_criteria": [
                        "composer audit sin vulnerabilidades",
                        "  composer.lock fija versiones  ",
                        "",
                        123,
                        {"description": "PSR-4 correcto"},
                    ],
                },
                {"id": "t2", "title": "Sin criterios"},
            ],
        }
    )
    t1 = next(t for t in out["tasks"] if t["id"] == "t1")
    assert t1["acceptance_criteria"] == [
        "composer audit sin vulnerabilidades",
        "composer.lock fija versiones",
        "PSR-4 correcto",
    ]
    t2 = next(t for t in out["tasks"] if t["id"] == "t2")
    assert t2["acceptance_criteria"] == []  # absent → [] (always present)


def test_normalise_plan_draft_caps_acceptance_criteria_count() -> None:
    out = _normalise_plan_draft(
        {
            "title": "x",
            "tasks": [
                {"id": "t1", "title": "T", "acceptance_criteria": [f"c{i}" for i in range(20)]}
            ],
        }
    )
    assert len(out["tasks"][0]["acceptance_criteria"]) <= 8


def test_pm_plan_draft_prompt_requests_acceptance_criteria() -> None:
    seen: list[list[Message]] = []
    model = LLMPlanningModel(provider=_FakeProvider("{}", seen=seen))  # type: ignore[arg-type]
    model.pm_plan_draft(_state([{"role": "user", "content": "x"}], set()), [])
    system = " ".join(m.content for m in seen[-1] if m.role == "system")
    assert "acceptance_criteria" in system


def test_normalise_plan_draft_coerces_complexity() -> None:
    # c11: a chat-planned task carries its own complexity estimate instead of
    # everything defaulting to `m`. Valid values are lowercased; invalid/absent
    # fall back to `m`.
    out = _normalise_plan_draft(
        {
            "title": "x",
            "tasks": [
                {"id": "t1", "title": "Grande", "complexity": "XL"},  # valid → lowercased
                {"id": "t2", "title": "Rara", "complexity": "enorme"},  # invalid → m
                {"id": "t3", "title": "Sin campo"},  # absent → m
            ],
        }
    )
    by_id = {t["id"]: t["complexity"] for t in out["tasks"]}
    assert by_id == {"t1": "xl", "t2": "m", "t3": "m"}


def test_pm_plan_draft_prompt_requests_complexity() -> None:
    seen: list[list[Message]] = []
    model = LLMPlanningModel(provider=_FakeProvider("{}", seen=seen))  # type: ignore[arg-type]
    model.pm_plan_draft(_state([{"role": "user", "content": "x"}], set()), [])
    system = " ".join(m.content for m in seen[-1] if m.role == "system")
    assert "complexity" in system


def test_normalise_plan_draft_extracts_phases() -> None:
    # c6: chat plans now carry phases[] so the `phase` sync scope works. Unknown
    # task ids and empty phases are dropped so sync_to_kanban never rejects them.
    out = _normalise_plan_draft(
        {
            "title": "x",
            "phases": [
                {"title": "Diseño", "tasks": ["t1", "ghost", "t1"]},  # unknown + dup dropped
                {"name": "Build", "tasks": ["t2"]},  # `name` → title
                {"title": "Vacía", "tasks": ["ghost"]},  # only unknown → dropped
                "garbage",  # non-dict → dropped
            ],
            "tasks": [
                {"id": "t1", "title": "A"},
                {"id": "t2", "title": "B"},
            ],
        }
    )
    assert out["phases"] == [
        {"title": "Diseño", "tasks": ["t1"]},
        {"title": "Build", "tasks": ["t2"]},
    ]


def test_normalise_plan_draft_phases_default_empty() -> None:
    out = _normalise_plan_draft({"title": "x", "tasks": [{"id": "t1", "title": "A"}]})
    assert out["phases"] == []  # absent → [] (phase scope simply unavailable)


def test_pm_plan_draft_prompt_requests_phases() -> None:
    seen: list[list[Message]] = []
    model = LLMPlanningModel(provider=_FakeProvider("{}", seen=seen))  # type: ignore[arg-type]
    model.pm_plan_draft(_state([{"role": "user", "content": "x"}], set()), [])
    system = " ".join(m.content for m in seen[-1] if m.role == "system")
    assert "phases" in system
