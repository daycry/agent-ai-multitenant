"""Integration tests for the S3 backup destination (Plan 12 task_12_05).

No real S3 / S3-compatible endpoint is reachable here, so the boto3 client is
MOCKED: :class:`S3Destination` takes a ``client_factory`` and the tests inject a
factory returning a :class:`MockS3Client` that records the calls it is handed and
fabricates plausible responses. The tests therefore assert:

  * UPLOAD — ``upload_file`` is called with the right local path, bucket, and the
    prefix-joined key; the returned :class:`UploadResult` carries the s3:// URI +
    size. (``upload_file`` is boto3's managed transfer = automatic multipart for
    large files, single PUT for small — Plan 12: "multipart for large files".)
  * LIST — ``list_remote`` paginates ``list_objects_v2`` on the prefix and returns
    one :class:`RemoteEntry` per object, with the prefix stripped from the name.
  * DOWNLOAD — ``download_file`` fetches the prefix-joined key to the local dest.
  * CONNECTIVITY — ``test_connectivity`` issues a cheap ``head_bucket`` and maps
    its outcome to a typed :class:`ConnectivityResult` (ok / not-ok, never raises).
  * ERROR MAPPING — a boto3 ``ClientError`` from any operation surfaces as a typed
    :class:`DestinationError` (or a not-ok ConnectivityResult for the probe).
  * CREDENTIALS — the access key + secret come from the secret seam
    (:class:`StaticSecretsProvider`), are handed to the client factory, and NEVER
    appear in plaintext config, the result, or the structlog output.
  * ENDPOINT_URL — a non-AWS endpoint_url is forwarded to the client factory, so
    any S3-compatible provider works.

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
    S3Destination,
    S3DestinationConfig,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# Distinctive secret values so a leak test can grep for them. These stand in for
# the Vault-resolved S3 credentials; they MUST NOT appear anywhere on disk or in
# logs.
_ACCESS_KEY = "AKIA-MUST-NOT-LEAK-0123456789"
_SECRET_KEY = "s3cr3t-secret-access-key-MUST-NOT-LEAK-abcdef0123456789"


class _ClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError — same shape (a single
    exception the adapter funnels into DestinationError) without importing
    botocore. Carries an operation name so the adapter's _safe_error has
    something realistic to render."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"An error occurred (AccessDenied) calling {operation}")
        self.operation = operation


class MockS3Client:
    """Records every call + fabricates boto3-shaped responses.

    ``fail_on`` is a method name; when set, calling that method raises a
    :class:`_ClientError` to exercise the adapter's error mapping. ``objects`` is
    the fake bucket contents ``list_objects_v2`` paginates over (a list of
    ``(key, size)``), split across two pages to prove pagination is honoured.
    """

    def __init__(
        self,
        *,
        build_kwargs: dict[str, Any],
        fail_on: str | None = None,
        objects: list[tuple[str, int]] | None = None,
    ) -> None:
        self.build_kwargs = build_kwargs
        self.fail_on = fail_on
        self.objects = objects or []
        self.upload_file_calls: list[tuple[str, str, str]] = []
        self.download_file_calls: list[tuple[str, str, str]] = []
        self.head_bucket_calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if self.fail_on == op:
            raise _ClientError(op)

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self._maybe_fail("upload_file")
        self.upload_file_calls.append((filename, bucket, key))

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self._maybe_fail("download_file")
        # Fabricate the downloaded file so the adapter's returned path exists.
        Path(filename).write_bytes(b"downloaded-bundle-bytes")
        self.download_file_calls.append((bucket, key, filename))

    def head_bucket(self, *, Bucket: str) -> dict[str, Any]:  # noqa: N803 — boto3 kwarg name
        self._maybe_fail("head_bucket")
        self.head_bucket_calls.append(Bucket)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_paginator(self, name: str) -> _MockPaginator:
        assert name == "list_objects_v2"
        self._maybe_fail("list_objects_v2")
        return _MockPaginator(self.objects)


class _MockPaginator:
    """Yields two pages so the adapter's pagination loop is exercised."""

    def __init__(self, objects: list[tuple[str, int]]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str) -> Any:  # noqa: N803 — boto3 kwarg names
        self.bucket = Bucket
        self.prefix = Prefix
        # Only return objects under the requested prefix (mirrors S3 semantics).
        matched = [(k, s) for (k, s) in self._objects if k.startswith(Prefix)]
        mid = (len(matched) + 1) // 2
        for chunk in (matched[:mid], matched[mid:]):
            yield {"Contents": [{"Key": k, "Size": s} for (k, s) in chunk]}


def _make_destination(
    tmp_path: Path,
    *,
    fail_on: str | None = None,
    objects: list[tuple[str, int]] | None = None,
    endpoint_url: str | None = None,
    region: str | None = "eu-west-1",
    prefix: str = "platform/backups",
) -> tuple[S3Destination, list[MockS3Client]]:
    """Build an S3Destination wired to a MockS3Client factory.

    Returns the destination + a list the factory appends each built client to, so
    a test can introspect the kwargs boto3 was handed (credentials, endpoint_url).
    """
    built: list[MockS3Client] = []

    def factory(**kwargs: Any) -> MockS3Client:
        client = MockS3Client(build_kwargs=kwargs, fail_on=fail_on, objects=objects)
        built.append(client)
        return client

    config = S3DestinationConfig(
        bucket="agentic-backups",
        prefix=prefix,
        endpoint_url=endpoint_url,
        region=region,
    )
    secrets = StaticSecretsProvider(
        values={
            config.access_key_field: _ACCESS_KEY,
            config.secret_key_field: _SECRET_KEY,
        }
    )
    dest = S3Destination(config=config, secrets=secrets, client_factory=factory)
    return dest, built


def _bundle(tmp_path: Path, name: str = "20260530T030000Z.tar.enc") -> Path:
    p = tmp_path / name
    p.write_bytes(b"encrypted-bundle-bytes")
    return p


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_puts_bundle_at_prefix_joined_key(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    bundle = _bundle(tmp_path)

    result = dest.upload(bundle)

    assert len(built) == 1
    client = built[0]
    # upload_file (managed transfer = automatic multipart for large files).
    assert len(client.upload_file_calls) == 1
    filename, bucket, key = client.upload_file_calls[0]
    assert filename == str(bundle)
    assert bucket == "agentic-backups"
    # Prefix joined with the bundle file name, single trailing slash.
    assert key == "platform/backups/20260530T030000Z.tar.enc"
    # Result carries the s3:// URI + the real byte size.
    assert result.remote_uri == "s3://agentic-backups/platform/backups/20260530T030000Z.tar.enc"
    assert result.size_bytes == bundle.stat().st_size
    assert result.destination == "s3"


def test_upload_rejects_a_directory_bundle(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path)
    a_dir = tmp_path / "20260530T030000Z"
    a_dir.mkdir()

    with pytest.raises(DestinationError, match="single bundle file"):
        dest.upload(a_dir)


def test_upload_maps_client_error_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="upload_file")
    bundle = _bundle(tmp_path)

    with pytest.raises(DestinationError, match="S3 upload to .* failed"):
        dest.upload(bundle)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_remote_paginates_and_strips_prefix(tmp_path: Path) -> None:
    objects = [
        ("platform/backups/20260528T030000Z.tar.enc", 100),
        ("platform/backups/20260529T030000Z.tar.enc", 200),
        ("platform/backups/20260530T030000Z.tar.enc", 300),
        ("platform/backups/", 0),  # folder placeholder — must be skipped
        ("other/unrelated.txt", 999),  # outside prefix — must not appear
    ]
    dest, _ = _make_destination(tmp_path, objects=objects)

    entries = dest.list_remote()

    assert entries == (
        RemoteEntry(name="20260528T030000Z.tar.enc", size_bytes=100),
        RemoteEntry(name="20260529T030000Z.tar.enc", size_bytes=200),
        RemoteEntry(name="20260530T030000Z.tar.enc", size_bytes=300),
    )


def test_list_remote_maps_client_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="list_objects_v2")

    with pytest.raises(DestinationError, match="S3 list of .* failed"):
        dest.list_remote()


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_fetches_prefix_joined_key_to_dest(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    out_dir = tmp_path / "restore"
    out_dir.mkdir()

    target = dest.download("20260530T030000Z.tar.enc", out_dir)

    client = built[0]
    assert len(client.download_file_calls) == 1
    bucket, key, filename = client.download_file_calls[0]
    assert bucket == "agentic-backups"
    assert key == "platform/backups/20260530T030000Z.tar.enc"
    # Written under the dest directory as the bare name.
    assert target == out_dir / "20260530T030000Z.tar.enc"
    assert target.is_file()
    assert filename == str(target)


def test_download_maps_client_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="download_file")

    with pytest.raises(DestinationError, match="S3 download of .* failed"):
        dest.download("missing.tar.enc", tmp_path)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_test_connectivity_ok_does_cheap_head_bucket(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    result = dest.test_connectivity()

    assert isinstance(result, ConnectivityResult)
    assert result.ok is True
    assert built[0].head_bucket_calls == ["agentic-backups"]


def test_test_connectivity_maps_error_without_raising(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="head_bucket")

    result = dest.test_connectivity()

    assert result.ok is False
    assert "head_bucket" in result.detail


def test_test_connectivity_reports_missing_credentials(tmp_path: Path) -> None:
    # Secret provider has NO S3 credentials → connectivity is a clean not-ok.
    config = S3DestinationConfig(bucket="agentic-backups", prefix="x")
    dest = S3Destination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        client_factory=lambda **_kw: MockS3Client(build_kwargs={}),
    )

    result = dest.test_connectivity()

    assert result.ok is False
    assert "credentials" in result.detail.lower()


# ---------------------------------------------------------------------------
# Credentials via the secret seam (never plaintext / logged)
# ---------------------------------------------------------------------------


def test_credentials_come_from_secret_seam_and_reach_the_client(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    # The factory (= boto3.client) was handed the secret-seam-resolved creds.
    assert kwargs["aws_access_key_id"] == _ACCESS_KEY
    assert kwargs["aws_secret_access_key"] == _SECRET_KEY
    assert kwargs["service_name"] == "s3"


def test_missing_credentials_map_to_destination_error_on_upload(tmp_path: Path) -> None:
    config = S3DestinationConfig(bucket="agentic-backups")
    dest = S3Destination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        client_factory=lambda **_kw: MockS3Client(build_kwargs={}),
    )

    with pytest.raises(DestinationError, match="missing S3 credentials|no value for S3"):
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
    assert _ACCESS_KEY not in blob
    assert _SECRET_KEY not in blob


# ---------------------------------------------------------------------------
# endpoint_url — any S3-compatible provider
# ---------------------------------------------------------------------------


def test_endpoint_url_forwarded_for_non_aws_provider(tmp_path: Path) -> None:
    dest, built = _make_destination(
        tmp_path, endpoint_url="https://s3.eu-central-003.backblazeb2.com", region="us-west-002"
    )

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    assert kwargs["endpoint_url"] == "https://s3.eu-central-003.backblazeb2.com"
    assert kwargs["region_name"] == "us-west-002"


def test_default_endpoint_url_is_none_for_aws(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path, endpoint_url=None)

    dest.upload(_bundle(tmp_path))

    assert built[0].build_kwargs["endpoint_url"] is None
