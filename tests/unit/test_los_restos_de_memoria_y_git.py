"""Restos de menor riesgo de la auditoría 2026-09-01, memoria y git (`task_cv_45`).

- E-11: nada filtraba secretos ni rutas de host en lo que se memoriza; el
  destilador corre fuera de los cuatro puntos del ciclo de guardrails y
  `memory_store` persistía `content` verbatim.
- E-07: el memorizer humano no heredó las correcciones F2.1/F2.3/llm-10: LLM
  del env, sin causa ni racha; las decisiones humanas morían en
  `ok:no_candidates`.
- G-10: nada vigilaba la caducidad de credenciales git; se descubría en el
  `pr_error` de un plan ya `completed`. Evento `git_credential_failed`,
  throttled.
- G-03: `direct_to_default_allowed` era una política fantasma: sin llamantes,
  y su `update-ref` sin guard FF habría retrocedido `main`. Se retira.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ------------------------------------------------------------- E-11 sanitize


def test_secrets_and_host_paths_are_redacted_before_persisting() -> None:
    from api_server.memorizer.sanitize import sanitize_memory_content

    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    text = f"usa el token {token} y edita /data/agent-platform/projects/acme/app/worktrees/t1/a.py"

    clean, redactions = sanitize_memory_content(text)

    assert token not in clean
    assert "[REDACTED:" in clean
    assert "/data/agent-platform/projects/acme/app" not in clean
    assert clean.endswith("<project-root>/worktrees/t1/a.py")
    assert redactions == 2


def test_clean_content_passes_through_untouched() -> None:
    from api_server.memorizer.sanitize import sanitize_memory_content

    clean, redactions = sanitize_memory_content("El endpoint /health devuelve 200.")

    assert clean == "El endpoint /health devuelve 200." and redactions == 0


def test_candidates_are_sanitised_on_the_way_to_the_row() -> None:
    from api_server.memorizer.distillation import MemoryCandidate
    from api_server.memorizer.persistence import _sanitized_candidates

    token = "AKIA" + "ABCDEFGHIJKLMNOP"
    cands = [MemoryCandidate(content=f"clave {token}", type="semantic", tags=("x",))]

    out, redactions = _sanitized_candidates(cands)

    assert redactions == 1
    assert token not in out[0].content and out[0].tags == ("x",)


# ------------------------------------------------------------- E-07 human memorizer


class _BrokenLLM:
    async def complete(self, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("ollama down")


class _GarbageLLM:
    async def complete(self, *_a: Any, **_k: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(content="no soy json")


def test_the_human_distillation_reports_its_cause() -> None:
    from api_server.memorizer.distillation import distil_human_work_session_result

    common = {
        "session": {"comments": "x", "hours_logged": 1, "output_files_attached": []},
        "agent": {"role": "qa"},
        "user": {"name": "Ana"},
    }
    broken = asyncio.run(distil_human_work_session_result(llm=_BrokenLLM(), **common))
    garbage = asyncio.run(distil_human_work_session_result(llm=_GarbageLLM(), **common))

    assert broken.candidates == [] and broken.cause == "llm_error"
    assert garbage.candidates == [] and garbage.cause == "llm_unparseable"


def test_the_human_memorizer_maps_the_cause_to_a_skip_reason() -> None:
    from workers.memorizer import _human_skip_reason

    assert _human_skip_reason("llm_error") == "skipped:llm_error"
    assert _human_skip_reason("llm_unparseable") == "skipped:llm_unparseable"
    assert _human_skip_reason("llm_empty") == "ok:no_candidates"


# ------------------------------------------------------------- G-10 git creds


@pytest.mark.parametrize(
    "text",
    [
        "GitHub PR falló (401): bad credentials",
        "fatal: Authentication failed for 'https://github.com/x/y.git/'",
        "remote: Permission to x/y denied to bot. fatal: unable to access: "
        "The requested URL returned error: 403",
        "git@github.com: Permission denied (publickey).",
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
    ],
)
def test_credential_failures_are_recognised(text: str) -> None:
    from workers.git_alerts import looks_like_git_credential_failure

    assert looks_like_git_credential_failure(text) is True


@pytest.mark.parametrize(
    "text", ["CONFLICT (content): Merge conflict in a.py", "timed out after 300s"]
)
def test_other_git_errors_are_not_credential_failures(text: str) -> None:
    from workers.git_alerts import looks_like_git_credential_failure

    assert looks_like_git_credential_failure(text) is False


def test_the_credential_event_is_emitted_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_server import celery_client
    from workers.git_alerts import notify_git_credential_failed

    emitted: list[dict[str, Any]] = []

    async def _record(payload: dict[str, Any]) -> bool:
        emitted.append(payload)
        return True

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _record)
    seen: set[str] = set()

    async def _throttle(key: str) -> bool:
        if key in seen:
            return False
        seen.add(key)
        return True

    async def _twice() -> None:
        await notify_git_credential_failed(
            tenant_id="t-1",
            subject="proyecto Acme",
            key="acme",
            reason="(401) bad credentials",
            throttle=_throttle,
        )
        await notify_git_credential_failed(
            tenant_id="t-1",
            subject="proyecto Acme",
            key="acme",
            reason="(401) bad credentials",
            throttle=_throttle,
        )

    asyncio.run(_twice())

    assert [e["event_type"] for e in emitted] == ["git_credential_failed"]
    assert emitted[0]["tenant_id"] == "t-1"
    assert emitted[0]["context"]["subject"] == "proyecto Acme"
    assert "401" in emitted[0]["context"]["reason"]


def test_the_event_has_templates_in_both_languages() -> None:
    from notification_dispatcher.event_mapping import EVENT_REGISTRY
    from notification_dispatcher.templates import BUILTIN_TEMPLATES

    assert "git_credential_failed" in EVENT_REGISTRY
    assert ("git_credential_failed", "es") in BUILTIN_TEMPLATES
    assert ("git_credential_failed", "en") in BUILTIN_TEMPLATES


# ------------------------------------------------------------- G-03 ghost policy


def test_direct_to_default_is_no_longer_a_push_policy() -> None:
    from workers.plan_git import PlanGitPolicies

    with pytest.raises(ValueError, match="direct_to_default_allowed"):
        PlanGitPolicies(push_policy="direct_to_default_allowed")  # type: ignore[arg-type]
    assert PlanGitPolicies(push_policy="forbidden").push_policy == "forbidden"


def test_the_api_rejects_the_retired_push_policy() -> None:
    from api_server.schemas.projects import GitConfigUpdateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GitConfigUpdateRequest(
            remote_url="https://github.com/acme/app.git",
            push_policy="direct_to_default_allowed",  # type: ignore[arg-type]
        )
    ok = GitConfigUpdateRequest(remote_url="https://github.com/acme/app.git")
    assert ok.push_policy == "branch_only_pr_required"
