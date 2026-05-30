"""Integration tests for the generic rclone backup destination (Plan 12 task_12_08).

No real rclone backend (Google Drive, B2, WebDAV, …) is reachable here, and
rclone is a subprocess rather than a Python client, so the
:class:`workers.backup.CommandRunner` seam is MOCKED:
:class:`RcloneDestination` takes a ``runner`` and the tests inject a
:class:`MockRunner` that records the argv it is handed (and, crucially, reads the
``--config`` temp file's contents AT CALL TIME, before the adapter deletes it)
and fabricates plausible rclone output. The tests therefore assert:

  * UPLOAD — ``rclone copy <bundle> <remote>:<path>`` is built with the right argv
    and the temp ``--config``; the returned :class:`UploadResult` carries the
    rclone:// URI + size.
  * LIST — ``rclone lsjson <remote>:<path>`` is built; its JSON output maps to one
    :class:`RemoteEntry` per regular file (directory entries skipped).
  * DOWNLOAD — ``rclone copy <remote>:<path>/<name> <local-dir>`` is built and the
    file lands under the local dest.
  * CONNECTIVITY — ``rclone lsd <remote>:<path>`` is built and the exit code maps
    to a typed :class:`ConnectivityResult` (never raises).
  * ERROR MAPPING — a non-zero rclone exit from any operation surfaces as a typed
    :class:`DestinationError` (or a not-ok ConnectivityResult for the probe).
  * CONFIG (CREDS) — the rclone config blob comes from the secret seam
    (:class:`StaticSecretsProvider`), is written to a TEMP FILE referenced via
    ``--config`` (NOT on the argv, NOT in the logs), and the temp file is REMOVED
    after the op (the path no longer exists once the call returns).

No real network, no real credentials, no real rclone binary.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from workers.backup import CommandResult
from workers.backup_destinations import (
    ConnectivityResult,
    DestinationError,
    RcloneDestination,
    RcloneDestinationConfig,
    RemoteEntry,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# A distinctive rclone config blob (an `rclone.conf` section body with OBSCURED
# creds) so a leak test can grep for it. This stands in for the Vault-resolved
# blob; it MUST NOT appear in the argv or the logs — only inside the temp file.
_CONFIG_BLOB = "\n".join(
    [
        "[gdrive]",
        "type = drive",
        # An rclone-obscured token stand-in — still a secret the leak test greps.
        "token = MUST-NOT-LEAK-obscured-0123456789abcdef",
        "client_secret = MUST-NOT-LEAK-secret-fedcba9876543210",
    ]
)


class _RecordedCall:
    """One recorded rclone invocation: the full argv + the temp config the
    adapter wrote (captured AT CALL TIME, before the adapter deletes it)."""

    def __init__(
        self,
        argv: list[str],
        config_path: Path | None,
        config_text: str | None,
        config_mode: int | None,
    ) -> None:
        self.argv = argv
        self.config_path = config_path
        self.config_text = config_text
        self.config_mode = config_mode


class MockRunner:
    """A fake :class:`workers.backup.CommandRunner`.

    Records every argv. For each call it locates the ``--config=<path>`` flag and
    reads the temp file's bytes + permission bits WHILE the call is in flight, so a
    test can prove (a) the creds were written to the file not the argv, and (b) the
    file existed (with 0600) during the op but is cleaned up afterwards. ``rc`` is
    the exit code to fabricate; ``stdout`` the output (e.g. an lsjson array).
    """

    def __init__(self, *, rc: int = 0, stdout: str = "") -> None:
        self._rc = rc
        self._stdout = stdout
        self.calls: list[_RecordedCall] = []

    def run(
        self,
        args: Any,
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        config_path: Path | None = None
        config_text: str | None = None
        config_mode: int | None = None
        for arg in argv:
            if arg.startswith("--config="):
                config_path = Path(arg.split("=", 1)[1])
                if config_path.is_file():
                    config_text = config_path.read_text(encoding="utf-8")
                    config_mode = config_path.stat().st_mode & 0o777
                break
        call = _RecordedCall(argv, config_path, config_text, config_mode)
        self.calls.append(call)
        return CommandResult(
            returncode=self._rc,
            stdout=self._stdout,
            stderr="" if self._rc == 0 else "rclone: directory not found / auth failed",
        )


def _make_destination(
    *,
    rc: int = 0,
    stdout: str = "",
    path: str = "platform/backups",
    remote: str = "gdrive",
    with_config: bool = True,
) -> tuple[RcloneDestination, MockRunner]:
    """Build an RcloneDestination wired to a MockRunner.

    Returns the destination + the runner so a test can introspect the argv +
    the temp-config contents each invocation was handed.
    """
    runner = MockRunner(rc=rc, stdout=stdout)
    config = RcloneDestinationConfig(remote=remote, path=path)
    values = {config.config_field: _CONFIG_BLOB} if with_config else {}
    secrets = StaticSecretsProvider(values=values)
    dest = RcloneDestination(config=config, secrets=secrets, runner=runner)
    return dest, runner


def _bundle(tmp_path: Path, name: str = "20260530T030000Z.tar.enc") -> Path:
    p = tmp_path / name
    p.write_bytes(b"encrypted-bundle-bytes")
    return p


def _lsjson(entries: list[tuple[str, int, bool]]) -> str:
    """Build an rclone ``lsjson`` array: (Name, Size, IsDir) tuples."""
    return json.dumps([{"Name": n, "Size": s, "IsDir": d} for (n, s, d) in entries])


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_builds_rclone_copy_argv(tmp_path: Path) -> None:
    dest, runner = _make_destination()
    bundle = _bundle(tmp_path)

    result = dest.upload(bundle)

    assert len(runner.calls) == 1
    argv = runner.calls[0].argv
    # rclone copy <bundle> <remote>:<path>, with the temp --config flag.
    assert argv[0] == "rclone"
    assert any(a.startswith("--config=") for a in argv)
    assert "copy" in argv
    assert str(bundle) in argv
    assert "gdrive:platform/backups" in argv
    # Result carries the rclone:// URI (file landed under the path) + real size.
    assert result.remote_uri == "rclone://gdrive:platform/backups/20260530T030000Z.tar.enc"
    assert result.size_bytes == bundle.stat().st_size
    assert result.destination == "rclone"


def test_upload_with_empty_path_targets_remote_root(tmp_path: Path) -> None:
    dest, runner = _make_destination(path="")
    dest.upload(_bundle(tmp_path))

    argv = runner.calls[0].argv
    # Empty path → bare "remote:" target.
    assert "gdrive:" in argv
    assert "gdrive:platform/backups" not in argv


def test_upload_rejects_a_directory_bundle(tmp_path: Path) -> None:
    dest, _ = _make_destination()
    a_dir = tmp_path / "20260530T030000Z"
    a_dir.mkdir()

    with pytest.raises(DestinationError, match="single bundle file"):
        dest.upload(a_dir)


def test_upload_maps_nonzero_exit_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(rc=1)
    bundle = _bundle(tmp_path)

    with pytest.raises(DestinationError, match=r"rclone copy to .* failed \(rc=1\)"):
        dest.upload(bundle)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_remote_builds_lsjson_and_skips_directories(tmp_path: Path) -> None:
    stdout = _lsjson(
        [
            ("20260528T030000Z.tar.enc", 100, False),
            ("20260529T030000Z.tar.enc", 200, False),
            ("nested-folder", 0, True),  # a directory — must be skipped
            ("20260530T030000Z.tar.enc", 300, False),
        ]
    )
    dest, runner = _make_destination(stdout=stdout)

    result = dest.list_remote()

    assert result == (
        RemoteEntry(name="20260528T030000Z.tar.enc", size_bytes=100),
        RemoteEntry(name="20260529T030000Z.tar.enc", size_bytes=200),
        RemoteEntry(name="20260530T030000Z.tar.enc", size_bytes=300),
    )
    argv = runner.calls[0].argv
    assert "lsjson" in argv
    assert "gdrive:platform/backups" in argv


def test_list_remote_handles_empty_output(tmp_path: Path) -> None:
    dest, _ = _make_destination(stdout="")
    assert dest.list_remote() == ()


def test_list_remote_maps_nonzero_exit_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(rc=3, stdout="")

    with pytest.raises(DestinationError, match=r"rclone lsjson of .* failed \(rc=3\)"):
        dest.list_remote()


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_builds_copy_back_argv(tmp_path: Path) -> None:
    dest, runner = _make_destination()
    out_dir = tmp_path / "restore"
    out_dir.mkdir()

    target = dest.download("20260530T030000Z.tar.enc", out_dir)

    argv = runner.calls[0].argv
    # rclone copy <remote:file> <local-dir>.
    assert "copy" in argv
    assert "gdrive:platform/backups/20260530T030000Z.tar.enc" in argv
    assert str(out_dir) in argv
    # Returned path is the file under the dest dir.
    assert target == out_dir / "20260530T030000Z.tar.enc"


def test_download_maps_nonzero_exit_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(rc=1)

    with pytest.raises(DestinationError, match=r"rclone copy of .* failed \(rc=1\)"):
        dest.download("missing.tar.enc", tmp_path)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_test_connectivity_ok_builds_lsd(tmp_path: Path) -> None:
    dest, runner = _make_destination()

    result = dest.test_connectivity()

    assert isinstance(result, ConnectivityResult)
    assert result.ok is True
    argv = runner.calls[0].argv
    assert "lsd" in argv
    assert "gdrive:platform/backups" in argv


def test_test_connectivity_maps_nonzero_exit_without_raising(tmp_path: Path) -> None:
    dest, _ = _make_destination(rc=1)

    result = dest.test_connectivity()

    assert result.ok is False
    assert "rc=1" in result.detail


def test_test_connectivity_reports_missing_config(tmp_path: Path) -> None:
    # Secret provider has NO rclone config blob → connectivity is a clean not-ok.
    dest, runner = _make_destination(with_config=False)

    result = dest.test_connectivity()

    assert result.ok is False
    assert "config" in result.detail.lower()
    # rclone was never invoked — the failure was at config resolution.
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Config blob (creds) — temp file, never argv/log, cleaned up
# ---------------------------------------------------------------------------


def test_config_blob_written_to_temp_file_not_argv(tmp_path: Path) -> None:
    dest, runner = _make_destination()

    dest.upload(_bundle(tmp_path))

    call = runner.calls[0]
    # The blob was written to the --config temp file...
    assert call.config_text == _CONFIG_BLOB
    # ...with owner-only perms (never group/other-readable). POSIX permission
    # bits are only meaningful off Windows (mirrors test_secrets_injection).
    if os.name != "nt":
        assert call.config_mode is not None
        assert (call.config_mode & 0o077) == 0
    # ...and the blob is NOWHERE on the argv.
    for arg in call.argv:
        assert _CONFIG_BLOB not in arg
        assert "MUST-NOT-LEAK" not in arg


def test_temp_config_file_is_cleaned_up_after_op(tmp_path: Path) -> None:
    dest, runner = _make_destination()

    dest.upload(_bundle(tmp_path))

    call = runner.calls[0]
    assert call.config_path is not None
    # The file existed during the call (we read it) but is gone afterwards.
    assert call.config_text == _CONFIG_BLOB
    assert not call.config_path.exists()


def test_missing_config_blob_maps_to_destination_error_on_upload(tmp_path: Path) -> None:
    dest, _ = _make_destination(with_config=False)

    with pytest.raises(DestinationError, match="rclone config blob"):
        dest.upload(_bundle(tmp_path))


def test_config_blob_never_leaks_to_result_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dest, _ = _make_destination(stdout=_lsjson([("20260530T030000Z.tar.enc", 300, False)]))
    bundle = _bundle(tmp_path)

    with caplog.at_level(logging.DEBUG):
        result = dest.upload(bundle)
        dest.list_remote()
        dest.test_connectivity()

    blob = repr(result) + "\n".join(r.getMessage() for r in caplog.records)
    assert _CONFIG_BLOB not in blob
    assert "MUST-NOT-LEAK" not in blob


# ---------------------------------------------------------------------------
# Config from settings + validation
# ---------------------------------------------------------------------------


def test_config_from_settings_reads_only_non_secret_tunables() -> None:
    class _Settings:
        backup_rclone_remote = "b2-offsite"
        backup_rclone_path = "/platform/backups/"

    config = RcloneDestinationConfig.from_settings(_Settings())

    assert config.remote == "b2-offsite"
    # normalized_path strips leading/trailing slashes.
    assert config.normalized_path() == "platform/backups"
    assert config.remote_root() == "b2-offsite:platform/backups"
    assert config.remote_file("x.tar.enc") == "b2-offsite:platform/backups/x.tar.enc"


def test_empty_remote_name_is_rejected_at_config_build() -> None:
    with pytest.raises(DestinationError, match="remote name"):
        RcloneDestinationConfig(remote="   ", path="backups")
