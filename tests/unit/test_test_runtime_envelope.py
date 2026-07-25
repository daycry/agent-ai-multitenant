"""El test-runtime hereda el envelope endurecido del agent-runtime (C-02).

El contenedor de test/stack ejecuta **el mismo código de usuario** que el
agent-runtime — la toolchain del proyecto sobre el worktree — y sin embargo su
envelope se quedaba corto en tres cosas que el del agente sí tiene:
``pids_limit`` (un `make -j` desbocado o una fork-bomb no tenían tope), y los
perfiles ``seccomp``/``apparmor`` configurados por el operador, que existían en
disco y no se aplicaban aquí.

El principio 2 de CLAUDE.md no distingue entre «contenedor del agente» y
«contenedor de tests»: los dos corren código que no controlamos. Que uno esté
endurecido y el otro no era una asimetría sin justificación, y la duplicación de
la lógica de perfiles entre los dos sitios es justo lo que hace que diverjan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from shared_test_runtimes import catalog
from workers import test_runtime
from workers.config import Settings
from workers.test_runtime import RuntimePlan

pytestmark = pytest.mark.unit


def _kwargs(settings: Settings | None = None) -> dict[str, Any]:
    resolved = settings or Settings()
    runner = test_runtime.TestRuntimeRunner(resolved)
    spec = test_runtime.TestRuntimeSpec(
        plan=RuntimePlan(template=catalog.get("php-phpunit"), checks=()),
        worktree_host_path="/data/worktrees/t1",
    )
    return runner._build_test_kwargs(spec, "bridge-test")


# ---------------------------------------------------------------------------
# pids_limit
# ---------------------------------------------------------------------------
def test_the_test_container_caps_its_process_count() -> None:
    """Sin tope, un `make -j` desbocado o una fork-bomb del repo bajo prueba se
    llevan por delante el host. El agent-runtime ya lo capaba; este no."""
    assert _kwargs()["pids_limit"] > 0


def test_the_cap_is_higher_than_the_agents() -> None:
    """Un contenedor de tests legítimamente arranca más procesos que el bucle del
    agente (compiladores en paralelo, watchers, servidores de prueba). Heredar el
    256 del agente cambiaría un riesgo por un falso negativo en los tests."""
    settings = Settings()
    assert _kwargs(settings)["pids_limit"] >= settings.container_pids_limit


def test_the_operator_can_tune_it() -> None:
    settings = Settings(test_runtime_pids_limit=999)
    assert _kwargs(settings)["pids_limit"] == 999


# ---------------------------------------------------------------------------
# seccomp / apparmor
# ---------------------------------------------------------------------------
def test_no_profiles_configured_keeps_the_baseline(tmp_path: Path) -> None:
    """Sin perfiles configurados el comportamiento no cambia (por defecto hoy)."""
    opts = _kwargs(Settings(seccomp_profile_path="", apparmor_profile=""))["security_opt"]
    assert opts == ["no-new-privileges:true"]


def test_the_configured_seccomp_profile_is_applied(tmp_path: Path) -> None:
    """Los perfiles endurecidos existen en disco desde prod-12 y este contenedor
    no los aplicaba: estaban escritos y desconectados."""
    profile = tmp_path / "seccomp.json"
    profile.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    opts = _kwargs(Settings(seccomp_profile_path=str(profile)))["security_opt"]
    assert any(o.startswith("seccomp=") for o in opts)
    # Se envía el CONTENIDO, no la ruta: el daemon no ve el fichero del worker.
    assert any("SCMP_ACT_ERRNO" in o for o in opts)


def test_the_configured_apparmor_profile_is_applied() -> None:
    opts = _kwargs(Settings(apparmor_profile="agentic-runtime"))["security_opt"]
    assert "apparmor=agentic-runtime" in opts


def test_no_new_privileges_survives_the_profiles() -> None:
    opts = _kwargs(Settings(apparmor_profile="agentic-runtime"))["security_opt"]
    assert "no-new-privileges:true" in opts


# ---------------------------------------------------------------------------
# El tronco común, para que no vuelvan a divergir
# ---------------------------------------------------------------------------
def test_both_envelopes_derive_their_profiles_from_the_same_helper(tmp_path: Path) -> None:
    """La duplicación es lo que hace que diverjan: C-02 existe porque la lógica
    de perfiles vivía solo en `isolation.py`."""
    from workers.isolation import build_hardened_run_kwargs, build_security_opt

    profile = tmp_path / "seccomp.json"
    profile.write_text('{"defaultAction":"SCMP_ACT_LOG"}', encoding="utf-8")
    settings = Settings(seccomp_profile_path=str(profile), apparmor_profile="agentic-runtime")

    shared = build_security_opt(settings)
    assert build_hardened_run_kwargs(settings)["security_opt"] == shared
    assert _kwargs(settings)["security_opt"] == shared


def test_the_baseline_envelope_is_still_there() -> None:
    kwargs = _kwargs()
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["read_only"] is True
    assert kwargs["user"] == "1000:1000"
