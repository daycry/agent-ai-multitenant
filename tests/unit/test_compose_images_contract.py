"""Contract: every app image the installer's compose references must have a
Dockerfile AND a build entry in release-images.yml (Plan prod-01 task_04).

This is the guard that keeps deploy-2 / quality-2 from silently reappearing —
the audit found the compose referencing ghcr.io/agentic-platform/* images the
repo could neither build nor publish."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release-images.yml"


def _app_images_from_compose() -> set[str]:
    from installer_backend.compose_generator import APP_IMAGE_REGISTRY, generate_compose
    from installer_backend.config import (
        InstallerConfig,
        OllamaProvider,
        ProvidersConfig,
        ResourceConfig,
        StorageConfig,
        SystemConfig,
        TenantConfig,
    )

    cfg = InstallerConfig(
        system=SystemConfig(domain="agentic.example.com"),
        resources=ResourceConfig(gpu_enabled=True),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
    )
    compose = generate_compose(cfg, monitoring=True)
    prefix = APP_IMAGE_REGISTRY + "/"
    apps: set[str] = set()
    for svc in compose["services"].values():
        image = str(svc.get("image", ""))
        if image.startswith(prefix):
            apps.add(image[len(prefix) :].split(":", 1)[0])
    return apps


def test_every_compose_app_image_has_dockerfile_and_release_entry() -> None:
    apps = _app_images_from_compose()
    assert apps, "no app images found in the generated compose"
    wf_text = WORKFLOW.read_text(encoding="utf-8")
    problems: list[str] = []
    for app in sorted(apps):
        if not (REPO / "apps" / app / "Dockerfile").is_file():
            problems.append(f"{app}: missing apps/{app}/Dockerfile (deploy-2)")
        if app not in wf_text:
            problems.append(f"{app}: not built/published in release-images.yml")
    assert not problems, "compose<->image contract broken:\n" + "\n".join(problems)
