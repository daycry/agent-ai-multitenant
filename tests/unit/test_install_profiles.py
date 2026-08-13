"""Install profile templates — scripts/install-profiles/*.yaml (Plan 15 task_15_11).

Phase B ships at least three ready-to-use ``install.yaml`` templates the
unattended CLI (task_15_10) accepts:

    * ``minimal.yaml``     — smallest viable host (1 worker, no GPU, 1 provider).
    * ``recommended.yaml`` — sensible production default (more workers, 2 providers).
    * ``gpu.yaml``         — GPU host (GPU enabled, large workers).

This is the automated companion to the plan's generic-shell check
(``ls scripts/install-profiles/ | wc -l | awk '$1>=3'``): it asserts there are
at least three profiles AND that every one of them actually LOADS + VALIDATES
through the SAME parser the CLI uses (:func:`installer_backend.cli.load_install_config`).
Loading runs the full Pydantic per-field validation plus the cross-field
provider rules (>= 1 ADR-0021 provider enabled with its credentials present), so
a profile that drifts out of the install-config schema fails here — never at the
operator's keyboard.

Nothing here touches a Docker host, Vault, or ``/data``: parsing a profile is
pure validation (no provisioning), exactly like the CLI's config gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from installer_backend.cli import load_install_config
from installer_backend.config import Environment, InstallerConfig

pytestmark = pytest.mark.unit

# scripts/install-profiles/ relative to the repo root (tests/unit/ → parents[2]).
_PROFILES_DIR = Path(__file__).resolve().parents[2] / "scripts" / "install-profiles"

#: The three profiles the plan requires, by name.
_REQUIRED_PROFILES = ("minimal.yaml", "recommended.yaml", "gpu.yaml")


def _profile_paths() -> list[Path]:
    return sorted(_PROFILES_DIR.glob("*.yaml"))


def _load(name: str) -> InstallerConfig:
    """Load a profile through the CLI's parser (full field + provider validation)."""

    return load_install_config((_PROFILES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The directory exists and ships at least three profiles (the plan check).
# ---------------------------------------------------------------------------
def test_profiles_directory_has_at_least_three_yaml_files() -> None:
    assert _PROFILES_DIR.is_dir(), f"falta el directorio de perfiles: {_PROFILES_DIR}"
    profiles = _profile_paths()
    assert len(profiles) >= 3, (
        f"se esperaban >= 3 perfiles en {_PROFILES_DIR}, se encontraron {len(profiles)}"
    )


def test_required_profiles_are_present() -> None:
    names = {p.name for p in _profile_paths()}
    for required in _REQUIRED_PROFILES:
        assert required in names, f"falta el perfil requerido: {required}"


# ---------------------------------------------------------------------------
# Every shipped profile loads + validates through the CLI parser.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", _REQUIRED_PROFILES)
def test_each_profile_loads_and_validates(name: str) -> None:
    config = _load(name)
    assert isinstance(config, InstallerConfig)
    # A loaded profile satisfies the ADR-0021 cross-field rule: at least one
    # provider enabled (load_install_config raises CliError otherwise).
    enabled_any = (
        config.providers.claude_sdk.enabled
        or config.providers.copilot.enabled
        or config.providers.azure_foundry.enabled
        or config.providers.ollama.enabled
    )
    assert enabled_any, f"{name}: ningún proveedor LLM habilitado"


def test_all_yaml_files_in_dir_load() -> None:
    """ANY *.yaml dropped in the profiles dir must be a valid install config.

    Guards against a future profile being added that silently drifts out of the
    install-config schema — the CLI would reject it at install time otherwise.
    """

    profiles = _profile_paths()
    assert profiles, "no se encontró ningún perfil .yaml"
    for path in profiles:
        config = load_install_config(path.read_text(encoding="utf-8"))
        assert isinstance(config, InstallerConfig), f"{path.name} no es un install config válido"


# ---------------------------------------------------------------------------
# The profiles genuinely DIFFER (resources / providers / GPU) per the contract.
# ---------------------------------------------------------------------------
def test_minimal_profile_is_smallest_no_gpu() -> None:
    config = _load("minimal.yaml")
    assert config.resources.gpu_enabled is False
    assert config.resources.worker_replicas == 1
    # Exactly one provider (the local Ollama) — the minimal footprint.
    assert config.providers.ollama.enabled is True
    assert config.providers.azure_foundry.enabled is False
    assert config.providers.claude_sdk.enabled is False
    assert config.providers.copilot.enabled is False


def test_recommended_profile_scales_up_no_gpu() -> None:
    config = _load("recommended.yaml")
    assert config.resources.gpu_enabled is False
    # More workers than minimal.
    assert config.resources.worker_replicas > _load("minimal.yaml").resources.worker_replicas
    # Two providers enabled (enterprise + local) for redundancy.
    enabled = [
        config.providers.claude_sdk.enabled,
        config.providers.copilot.enabled,
        config.providers.azure_foundry.enabled,
        config.providers.ollama.enabled,
    ]
    assert sum(enabled) >= 2


def test_gpu_profile_enables_gpu() -> None:
    config = _load("gpu.yaml")
    assert config.resources.gpu_enabled is True
    # The GPU host is sized larger than minimal.
    assert config.resources.worker_replicas > _load("minimal.yaml").resources.worker_replicas
    assert config.providers.ollama.enabled is True


def test_profiles_target_production_environment() -> None:
    for name in _REQUIRED_PROFILES:
        assert _load(name).system.environment is Environment.PRODUCTION


# ---------------------------------------------------------------------------
# The shipped profiles carry only PLACEHOLDER secrets (never real secrets).
# ---------------------------------------------------------------------------
def test_profiles_ship_placeholder_secrets_only() -> None:
    """Committed profiles must use obvious CHANGE_ME placeholders, never secrets."""

    for path in _profile_paths():
        text = path.read_text(encoding="utf-8")
        # The MinIO secret line must point at a placeholder, not a real value.
        assert "CHANGE_ME" in text, f"{path.name} debe usar marcadores CHANGE_ME para secretos"
