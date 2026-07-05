"""ADR 0103 — recalibración SAFE de las guardas de research (G2/G3b/G4a).

- G4a: search_code cuenta como research (gana novedad) y NO es mutador.
- G3b: un fallo de PLATAFORMA (tool denegada/ausente, EACCES) no acumula esterilidad;
  un file-not-found de un path ADIVINADO por el agente sí (anti-gaming, r5a).
- G2: un turno productivo (write/stack_exec ok) decae los contadores per-target, para
  que un bucle TDD legítimo no dispare el same-target nudge/trip.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.graph import (
    _AgentLoop,
    _is_mutating_tool,
    _is_platform_error,
    _is_research_tool,
    _read_target,
)


def _loop() -> _AgentLoop:
    deps = SimpleNamespace(is_review=False)
    return _AgentLoop(deps, tracker=SimpleNamespace(), detector=SimpleNamespace())  # type: ignore[arg-type]


# --- G4a -------------------------------------------------------------------
def test_search_code_is_research_and_not_mutating() -> None:
    assert _is_research_tool("search_code")
    assert not _is_mutating_tool("search_code")


def test_read_target_search_code() -> None:
    assert _read_target("search_code", {"query": "login"}) == "search_code:login"
    assert _read_target("search_code", {"pattern": "def foo"}) == "search_code:def foo"
    assert _read_target("search_code", {}) is None


# --- G3b -------------------------------------------------------------------
def test_is_platform_error_signatures() -> None:
    assert _is_platform_error({"error": "command not allowed: sed"})
    assert _is_platform_error({"error": "unknown tool: search_code"})
    assert _is_platform_error({"error": "Permission denied"})
    assert not _is_platform_error({"error": "file not found: nope.php"})
    assert not _is_platform_error({"ok": True})


def test_platform_error_does_not_accumulate_sterility() -> None:
    loop = _loop()
    loop._track_research(
        "read_file", {"tool_args": {"path": "x"}}, {"ok": False, "error": "command not allowed"}
    )
    assert loop.read_churn_streak == 0
    assert loop.read_counts == {}


def test_guessed_missing_path_still_sterile() -> None:
    loop = _loop()
    loop._track_research(
        "read_file",
        {"tool_args": {"path": "nope.php"}},
        {"ok": False, "error": "file not found: nope.php"},
    )
    assert loop.read_churn_streak == 1


# --- G2 --------------------------------------------------------------------
def test_production_decays_per_target_read_counts() -> None:
    loop = _loop()
    loop._track_research("read_file", {"tool_args": {"path": "a"}}, {"ok": True, "output": "x"})
    loop._track_research("read_file", {"tool_args": {"path": "a"}}, {"ok": True, "output": "x"})
    assert loop.read_counts.get("read_file:a", 0) == 2
    # A productive write clears the per-target read pressure (legit TDD re-read loop).
    loop._track_research("write_file", {"tool_args": {"path": "a"}}, {"ok": True})
    assert loop.read_counts == {}
    assert loop.has_produced is True
