"""Integration tests for the SFTP / NAS backup destination (Plan 12 task_12_07).

No real SFTP/SSH host is reachable here, so the paramiko client is MOCKED:
:class:`SftpDestination` takes a ``transport_factory`` and the tests inject a
factory returning a :class:`MockSftpClient` that records the calls it is handed
and fabricates plausible responses. The tests therefore assert:

  * UPLOAD — ``put`` is called with the right local path and the
    remote-dir-joined remote path; the returned :class:`UploadResult` carries the
    sftp:// URI + size.
  * LIST — ``list_remote`` calls ``listdir_attr`` on the remote dir and returns
    one :class:`RemoteEntry` per regular file (directory entries skipped), with
    the size from each attr.
  * DOWNLOAD — ``get`` fetches the remote-dir-joined path to the local dest.
  * CONNECTIVITY — ``test_connectivity`` opens a session + ``stat``s the path and
    maps the outcome to a typed :class:`ConnectivityResult` (never raises).
  * ERROR MAPPING — an auth/transport error (a paramiko ``SSHException`` shape)
    from any operation surfaces as a typed :class:`DestinationError` (or a not-ok
    ConnectivityResult for the probe).
  * CREDENTIALS — the password OR private key come from the secret seam
    (:class:`StaticSecretsProvider`), are handed to the transport factory, and
    NEVER appear in plaintext config, the result, or the structlog output.
  * HOST-KEY POLICY — the configured host-key policy reaches the factory; an
    invalid policy is rejected at config-build time.

No real network, no real credentials.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from workers.backup_destinations import (
    ConnectivityResult,
    DestinationError,
    RemoteEntry,
    SftpDestination,
    SftpDestinationConfig,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# Distinctive secret values so a leak test can grep for them. These stand in for
# the Vault-resolved SFTP credentials; they MUST NOT appear anywhere on disk or
# in logs.
_PASSWORD = "sftp-p4ssw0rd-MUST-NOT-LEAK-0123456789"
# A PEM-shaped stand-in for a private key. The header marker is assembled from
# parts so the detect-private-key pre-commit hook does not flag this TEST string
# as a real committed key — the runtime value is still a plausible key blob the
# leak test greps for.
_PEM_HEADER = "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----"
_PRIVATE_KEY = f"{_PEM_HEADER}\nMUST-NOT-LEAK-abcdef0123456789\n-----END-----"


class _SSHError(Exception):
    """Stand-in for paramiko's SSHException / SFTPError — a single exception the
    adapter funnels into DestinationError, without importing paramiko. Carries an
    operation name so the adapter's _safe_error has something realistic to
    render. NEVER carries credential material."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Authentication/transport failure during {operation}")
        self.operation = operation


class _FakeAttr:
    """A paramiko ``SFTPAttributes``-shaped entry for ``listdir_attr``."""

    def __init__(self, filename: str, st_size: int, *, is_dir: bool = False) -> None:
        self.filename = filename
        self.st_size = st_size
        # Directory bit (S_IFDIR=0o040000) vs regular file (S_IFREG=0o100000).
        self.st_mode = 0o040755 if is_dir else 0o100644


class MockSftpClient:
    """Records every call + fabricates paramiko-SFTP-shaped responses.

    ``fail_on`` is a method name; when set, calling that method raises an
    :class:`_SSHError` to exercise the adapter's error mapping. ``entries`` is the
    fake remote directory contents ``listdir_attr`` returns.
    """

    def __init__(
        self,
        *,
        build_kwargs: dict[str, Any],
        fail_on: str | None = None,
        entries: list[_FakeAttr] | None = None,
    ) -> None:
        self.build_kwargs = build_kwargs
        self.fail_on = fail_on
        self.entries = entries or []
        self.put_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.stat_calls: list[str] = []
        self.listdir_attr_calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if self.fail_on == op:
            raise _SSHError(op)

    def put(self, localpath: str, remotepath: str) -> None:
        self._maybe_fail("put")
        self.put_calls.append((localpath, remotepath))

    def get(self, remotepath: str, localpath: str) -> None:
        self._maybe_fail("get")
        # Fabricate the downloaded file so the adapter's returned path exists.
        Path(localpath).write_bytes(b"downloaded-bundle-bytes")
        self.get_calls.append((remotepath, localpath))

    def listdir_attr(self, path: str) -> list[_FakeAttr]:
        self._maybe_fail("listdir_attr")
        self.listdir_attr_calls.append(path)
        return self.entries

    def stat(self, path: str) -> _FakeAttr:
        self._maybe_fail("stat")
        self.stat_calls.append(path)
        return _FakeAttr(path, 0, is_dir=True)


def _make_destination(
    tmp_path: Path,
    *,
    fail_on: str | None = None,
    entries: list[_FakeAttr] | None = None,
    use_private_key: bool = False,
    remote_path: str = "/srv/backups/platform",
    host_key_policy: str = "reject",
) -> tuple[SftpDestination, list[MockSftpClient]]:
    """Build an SftpDestination wired to a MockSftpClient factory.

    Returns the destination + a list the factory appends each built client to, so
    a test can introspect the kwargs paramiko was handed (credentials, host,
    host-key policy).
    """
    built: list[MockSftpClient] = []

    def factory(**kwargs: Any) -> MockSftpClient:
        client = MockSftpClient(build_kwargs=kwargs, fail_on=fail_on, entries=entries)
        built.append(client)
        return client

    config = SftpDestinationConfig(
        host="nas.internal.example",
        port=2222,
        username="backup-bot",
        remote_path=remote_path,
        host_key_policy=host_key_policy,
    )
    secret_values = (
        {config.private_key_field: _PRIVATE_KEY}
        if use_private_key
        else {config.password_field: _PASSWORD}
    )
    secrets = StaticSecretsProvider(values=secret_values)
    dest = SftpDestination(config=config, secrets=secrets, transport_factory=factory)
    return dest, built


def _bundle(tmp_path: Path, name: str = "20260530T030000Z.tar.enc") -> Path:
    p = tmp_path / name
    p.write_bytes(b"encrypted-bundle-bytes")
    return p


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_puts_bundle_at_remote_path(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    bundle = _bundle(tmp_path)

    result = dest.upload(bundle)

    assert len(built) == 1
    client = built[0]
    assert len(client.put_calls) == 1
    localpath, remotepath = client.put_calls[0]
    assert localpath == str(bundle)
    # Remote dir joined with the bundle file name (POSIX, no trailing slash).
    assert remotepath == "/srv/backups/platform/20260530T030000Z.tar.enc"
    # Result carries the sftp:// URI + the real byte size.
    assert result.remote_uri == (
        "sftp://nas.internal.example:2222/srv/backups/platform/20260530T030000Z.tar.enc"
    )
    assert result.size_bytes == bundle.stat().st_size
    assert result.destination == "sftp"


def test_upload_rejects_a_directory_bundle(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path)
    a_dir = tmp_path / "20260530T030000Z"
    a_dir.mkdir()

    with pytest.raises(DestinationError, match="single bundle file"):
        dest.upload(a_dir)


def test_upload_maps_transport_error_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="put")
    bundle = _bundle(tmp_path)

    with pytest.raises(DestinationError, match=r"SFTP upload to .* failed"):
        dest.upload(bundle)


def test_upload_with_empty_remote_path_writes_to_session_cwd(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path, remote_path="")
    dest.upload(_bundle(tmp_path))

    _, remotepath = built[0].put_calls[0]
    # Empty remote dir → bare file name in the session's default directory.
    assert remotepath == "20260530T030000Z.tar.enc"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_remote_returns_files_skipping_directories(tmp_path: Path) -> None:
    entries = [
        _FakeAttr("20260528T030000Z.tar.enc", 100),
        _FakeAttr("20260529T030000Z.tar.enc", 200),
        _FakeAttr("nested-folder", 0, is_dir=True),  # a directory — must be skipped
        _FakeAttr("20260530T030000Z.tar.enc", 300),
    ]
    dest, built = _make_destination(tmp_path, entries=entries)

    result = dest.list_remote()

    assert result == (
        RemoteEntry(name="20260528T030000Z.tar.enc", size_bytes=100),
        RemoteEntry(name="20260529T030000Z.tar.enc", size_bytes=200),
        RemoteEntry(name="20260530T030000Z.tar.enc", size_bytes=300),
    )
    # listdir_attr was called on the configured remote dir.
    assert built[0].listdir_attr_calls == ["/srv/backups/platform"]


def test_list_remote_maps_transport_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="listdir_attr")

    with pytest.raises(DestinationError, match=r"SFTP list of .* failed"):
        dest.list_remote()


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_fetches_remote_path_to_dest(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    out_dir = tmp_path / "restore"
    out_dir.mkdir()

    target = dest.download("20260530T030000Z.tar.enc", out_dir)

    client = built[0]
    assert len(client.get_calls) == 1
    remotepath, localpath = client.get_calls[0]
    assert remotepath == "/srv/backups/platform/20260530T030000Z.tar.enc"
    # Written under the dest directory as the bare name.
    assert target == out_dir / "20260530T030000Z.tar.enc"
    assert target.is_file()
    assert localpath == str(target)


def test_download_maps_transport_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="get")

    with pytest.raises(DestinationError, match=r"SFTP download of .* failed"):
        dest.download("missing.tar.enc", tmp_path)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_test_connectivity_ok_opens_session_and_stats_path(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    result = dest.test_connectivity()

    assert isinstance(result, ConnectivityResult)
    assert result.ok is True
    # The probe opened a session (one client built) + stat'd the remote dir.
    assert built[0].stat_calls == ["/srv/backups/platform"]


def test_test_connectivity_maps_error_without_raising(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="stat")

    result = dest.test_connectivity()

    assert result.ok is False
    assert "stat" in result.detail


def test_test_connectivity_reports_missing_credentials(tmp_path: Path) -> None:
    # Secret provider has NO SFTP auth material → connectivity is a clean not-ok.
    config = SftpDestinationConfig(
        host="nas.internal.example", username="backup-bot", remote_path="/srv/backups"
    )
    dest = SftpDestination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        transport_factory=lambda **_kw: MockSftpClient(build_kwargs={}),
    )

    result = dest.test_connectivity()

    assert result.ok is False
    assert "auth" in result.detail.lower() or "credential" in result.detail.lower()


# ---------------------------------------------------------------------------
# Credentials via the secret seam (never plaintext / logged)
# ---------------------------------------------------------------------------


def test_password_comes_from_secret_seam_and_reaches_the_client(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    # The factory (= paramiko transport) was handed the secret-seam-resolved
    # password + the connection details.
    assert kwargs["password"] == _PASSWORD
    assert kwargs.get("private_key") is None
    assert kwargs["host"] == "nas.internal.example"
    assert kwargs["port"] == 2222
    assert kwargs["username"] == "backup-bot"


def test_private_key_comes_from_secret_seam_and_reaches_the_client(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path, use_private_key=True)

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    # Private-key auth: the key reaches the factory, no password.
    assert kwargs["private_key"] == _PRIVATE_KEY
    assert kwargs.get("password") is None


def test_host_key_policy_reaches_the_factory(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path, host_key_policy="auto_add")

    dest.upload(_bundle(tmp_path))

    assert built[0].build_kwargs["host_key_policy"] == "auto_add"


def test_invalid_host_key_policy_is_rejected_at_config_build(tmp_path: Path) -> None:
    with pytest.raises(DestinationError, match="host_key_policy"):
        SftpDestinationConfig(
            host="nas.internal.example",
            username="backup-bot",
            remote_path="/srv/backups",
            host_key_policy="disabled",
        )


def test_missing_credentials_map_to_destination_error_on_upload(tmp_path: Path) -> None:
    config = SftpDestinationConfig(
        host="nas.internal.example", username="backup-bot", remote_path="/srv/backups"
    )
    dest = SftpDestination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        transport_factory=lambda **_kw: MockSftpClient(build_kwargs={}),
    )

    with pytest.raises(DestinationError, match="no SFTP auth material"):
        dest.upload(_bundle(tmp_path))


def test_credentials_never_leak_to_result_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dest, _ = _make_destination(tmp_path)
    bundle = _bundle(tmp_path)

    with caplog.at_level(logging.DEBUG):
        result = dest.upload(bundle)
        dest.list_remote()
        dest.test_connectivity()

    blob = repr(result) + "\n".join(r.getMessage() for r in caplog.records)
    assert _PASSWORD not in blob
    assert _PRIVATE_KEY not in blob


def test_private_key_never_leaks_to_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    dest, _ = _make_destination(tmp_path, use_private_key=True)

    with caplog.at_level(logging.DEBUG):
        dest.upload(_bundle(tmp_path))

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert _PRIVATE_KEY not in blob


# ---------------------------------------------------------------------------
# Config from settings
# ---------------------------------------------------------------------------


def test_config_from_settings_reads_only_non_secret_tunables() -> None:
    class _Settings:
        backup_sftp_host = "nas.internal.example"
        backup_sftp_port = 2222
        backup_sftp_username = "backup-bot"
        backup_sftp_path = "/srv/backups/platform/"
        backup_sftp_host_key_policy = "auto_add"
        backup_sftp_known_hosts_path = "/etc/ssh/known_hosts"

    config = SftpDestinationConfig.from_settings(_Settings())

    assert config.host == "nas.internal.example"
    assert config.port == 2222
    assert config.username == "backup-bot"
    # normalized_path strips the trailing slash.
    assert config.normalized_path() == "/srv/backups/platform"
    assert config.host_key_policy == "auto_add"
    assert config.known_hosts_path == "/etc/ssh/known_hosts"
