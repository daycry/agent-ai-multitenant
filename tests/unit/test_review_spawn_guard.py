"""hallazgo #4: sin ``main_image`` el worker NO lanza contenedor de review.

El placeholder ``alpine:3.20`` está retirado: ``resolve_review_main_image``
devuelve ``None`` cuando el proyecto no pinea imagen, el request viaja con
``main_image=None`` y el spawn debe cortocircuitar sin tocar el daemon.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_spawn_skips_without_main_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.tasks import review_runtime_task as mod

    def _boom() -> None:
        raise AssertionError("sin main_image el docker client NO debe tocarse")

    monkeypatch.setattr(mod, "get_docker_client", _boom)
    out = mod._spawn_review_runtime(
        {"main_image": None, "worktree_host_path": ""},
        "sid-1",
        object(),  # type: ignore[arg-type]
    )
    assert out == ()


def test_spawn_skips_on_blank_main_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.tasks import review_runtime_task as mod

    def _boom() -> None:
        raise AssertionError("con main_image en blanco el docker client NO debe tocarse")

    monkeypatch.setattr(mod, "get_docker_client", _boom)
    out = mod._spawn_review_runtime(
        {"main_image": "   ", "worktree_host_path": ""},
        "sid-2",
        object(),  # type: ignore[arg-type]
    )
    assert out == ()
