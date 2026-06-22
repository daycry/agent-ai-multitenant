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


def test_normalise_plan_draft_empty_when_no_tasks() -> None:
    out = _normalise_plan_draft({"title": "x"})
    assert out["tasks"] == []
    assert out["title"] == "x"
