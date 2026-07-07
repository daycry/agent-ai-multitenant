"""El bloque PROGRESS acota cuántos ficheros/digests muestra (hallazgo H5).

El cap estaba hardcodeado como ``12`` suelto en dos sitios de
``_progress_summary`` — ahora es una constante nombrada y este test fija el
comportamiento de elisión (el ``(+N more)``) contra ella.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.graph import _PROGRESS_FILES_MAX, _AgentLoop


def _loop() -> _AgentLoop:
    deps = SimpleNamespace(is_review=False)
    tracker = SimpleNamespace(
        usage=SimpleNamespace(iterations=3),
        budgets=SimpleNamespace(max_iterations=50),
    )
    return _AgentLoop(deps, tracker=tracker, detector=SimpleNamespace())  # type: ignore[arg-type]


def test_progress_elides_written_files_beyond_cap() -> None:
    loop = _loop()
    loop.written_files = {f"f{i:02d}.py" for i in range(_PROGRESS_FILES_MAX + 3)}
    summary = loop._progress_summary()
    assert "(+3 more)" in summary


def test_progress_shows_all_files_under_cap() -> None:
    loop = _loop()
    loop.written_files = {"a.py", "b.py"}
    summary = loop._progress_summary()
    assert "a.py" in summary and "b.py" in summary
    assert "more)" not in summary
