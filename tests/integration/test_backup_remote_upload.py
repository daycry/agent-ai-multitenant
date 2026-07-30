"""Integration test — offsite backup upload wired to the beat (prod-04 task_prod_04_12).

Fase B del Plan 12 construyó los adaptadores de destino (S3/B2/SFTP/rclone) pero la
subida del bundle verificado al destino remoto tras el backup diario quedó sin
cablear: ``backup_destinations`` era código muerto en producción y el bundle nunca
salía de la máquina. Aquí ejercitamos el helper que empaqueta el bundle verificado y
lo sube a cada destino habilitado, best-effort (un destino que falla no tumba el run).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


class _FakeDest:
    """Registra las llamadas a upload() en vez de tocar la red."""

    def __init__(self, name: str, *, boom: bool = False) -> None:
        self.name = name
        self.boom = boom
        self.uploaded: list[Path] = []

    def upload(self, bundle_path: Path):
        from workers.backup_destinations import DestinationError, UploadResult

        if self.boom:
            raise DestinationError(f"{self.name} down")
        self.uploaded.append(Path(bundle_path))
        return UploadResult(destination=self.name, remote_uri=f"fake://{self.name}", size_bytes=1)


def _fake_result(tmp_path: Path) -> SimpleNamespace:
    """Un bundle local ya escrito: <backup_root>/<backup_id>/ con un fichero."""
    backup_id = "20260707T030000Z"
    bundle_dir = tmp_path / backup_id
    bundle_dir.mkdir()
    (bundle_dir / "manifest.json").write_text('{"backup_id": "20260707T030000Z"}')
    (bundle_dir / "pg.dump").write_bytes(b"\x00\x01\x02")
    return SimpleNamespace(backup_id=backup_id, bundle_dir=bundle_dir)


@pytest.mark.asyncio
async def test_uploads_verified_bundle_to_enabled_destinations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers import backup_task

    built: dict[str, _FakeDest] = {"s3-primary": _FakeDest("s3-primary")}

    def _fake_build(config, *, secrets, runner=None):
        return built[config["name"]]

    monkeypatch.setattr("workers.backup_destinations.build_destination", _fake_build)

    destinations = [
        {"type": "s3", "name": "s3-primary", "enabled": True, "config": {"bucket": "b"}}
    ]
    uploaded, failed = await backup_task._upload_bundle_to_destinations(
        _fake_result(tmp_path), destinations
    )

    assert uploaded == ["s3-primary"]
    assert failed == []
    # Se subió UN fichero único llamado <backup_id>.tar (casa con _strip_bundle_suffix).
    assert len(built["s3-primary"].uploaded) == 1
    assert built["s3-primary"].uploaded[0].name == "20260707T030000Z.tar"


@pytest.mark.asyncio
async def test_disabled_destination_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers import backup_task

    off = _FakeDest("archive")

    monkeypatch.setattr(
        "workers.backup_destinations.build_destination",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe construirse un destino off")),
    )

    destinations = [{"type": "s3", "name": "archive", "enabled": False, "config": {"bucket": "b"}}]
    uploaded, failed = await backup_task._upload_bundle_to_destinations(
        _fake_result(tmp_path), destinations
    )

    assert uploaded == []
    assert failed == []
    assert off.uploaded == []


@pytest.mark.asyncio
async def test_failing_destination_is_captured_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from workers import backup_task

    dests = {"ok": _FakeDest("ok"), "bad": _FakeDest("bad", boom=True)}
    monkeypatch.setattr(
        "workers.backup_destinations.build_destination",
        lambda config, **k: dests[config["name"]],
    )

    destinations = [
        {"type": "s3", "name": "ok", "enabled": True, "config": {"bucket": "b"}},
        {"type": "sftp", "name": "bad", "enabled": True, "config": {"host": "h"}},
    ]
    uploaded, failed = await backup_task._upload_bundle_to_destinations(
        _fake_result(tmp_path), destinations
    )

    # El fallo se captura → aparece en failed; el run NO revienta y el otro sí sube.
    assert uploaded == ["ok"]
    assert failed == ["bad"]


@pytest.mark.asyncio
async def test_no_destinations_is_noop(tmp_path: Path) -> None:
    from workers import backup_task

    uploaded, failed = await backup_task._upload_bundle_to_destinations(_fake_result(tmp_path), [])
    assert uploaded == []
    assert failed == []
