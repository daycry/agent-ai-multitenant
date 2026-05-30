"""Integration tests for the Backblaze B2 backup destination (Plan 12 task_12_06).

B2 is S3-COMPATIBLE, so :class:`B2Destination` REUSES the S3 adapter
(:class:`workers.backup_destinations.S3Destination`) via B2's S3-compatible
endpoint. No real B2 endpoint is reachable here, so the boto3 client is MOCKED:
the destination takes a ``client_factory`` and the tests inject a factory
returning a :class:`MockB2Client` that records calls + fabricates responses.

These tests assert the SHARED behaviour still works against B2 AND the three B2
quirks the adapter layers on (Plan 12: "es S3-compatible pero con quirks"):

  * ENDPOINT (quirk #1) — the operator configures only a REGION; the adapter
    forwards the derived ``s3.<region>.backblazeb2.com`` endpoint + matching
    region to the client factory (= boto3.client).
  * MULTIPART PART SIZE (quirk #2) — ``upload`` hands ``upload_file`` a boto3
    ``TransferConfig`` pinned to B2-friendly part sizing, not the AWS default.
  * AUTH (quirk #3) — the application keyId / key are resolved from the
    B2-SPECIFIC secret-seam field names (distinct from the S3 ones), handed to
    the client, and NEVER appear in plaintext config, the result, or the logs.
  * UPLOAD / LIST / DOWNLOAD / CONNECTIVITY — work exactly as for S3 (the reused
    code path) but against the b2:// URI scheme.
  * ERROR MAPPING — a client error from any operation surfaces as a typed
    :class:`DestinationError` (or a not-ok ConnectivityResult for the probe).

No real network, no real credentials.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from workers.backup_destinations import (
    B2_APPLICATION_KEY_FIELD,
    B2_KEY_ID_FIELD,
    B2_MULTIPART_CHUNKSIZE_BYTES,
    B2_MULTIPART_THRESHOLD_BYTES,
    B2Destination,
    B2DestinationConfig,
    ConnectivityResult,
    DestinationError,
    RemoteEntry,
    b2_endpoint_url,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# Distinctive B2 application-key values so a leak test can grep for them. These
# stand in for the Vault-resolved B2 credentials; they MUST NOT appear anywhere
# on disk or in logs.
_KEY_ID = "0011-B2-KEYID-MUST-NOT-LEAK-0123456789"
_APP_KEY = "K001-b2-application-key-MUST-NOT-LEAK-abcdef0123456789"

_REGION = "us-west-002"
_ENDPOINT = "https://s3.us-west-002.backblazeb2.com"


class _ClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError — same shape the adapter
    funnels into DestinationError, without importing botocore."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"An error occurred (AccessDenied) calling {operation}")
        self.operation = operation


class MockB2Client:
    """Records calls + fabricates boto3-shaped responses for the B2 S3 endpoint.

    Crucially, ``upload_file`` accepts the boto3 ``Config`` kwarg (the
    TransferConfig the B2 adapter injects for quirk #2) so the test can assert the
    B2 part sizing is applied. Otherwise mirrors the S3 mock.
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
        self.upload_file_config: Any | None = None
        self.download_file_calls: list[tuple[str, str, str]] = []
        self.head_bucket_calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        if self.fail_on == op:
            raise _ClientError(op)

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        Config: Any = None,  # noqa: N803 — boto3 kwarg
    ) -> None:
        self._maybe_fail("upload_file")
        self.upload_file_calls.append((filename, bucket, key))
        self.upload_file_config = Config

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self._maybe_fail("download_file")
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
    """Yields two pages so the (reused) pagination loop is exercised."""

    def __init__(self, objects: list[tuple[str, int]]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str) -> Any:  # noqa: N803 — boto3 kwarg names
        matched = [(k, s) for (k, s) in self._objects if k.startswith(Prefix)]
        mid = (len(matched) + 1) // 2
        for chunk in (matched[:mid], matched[mid:]):
            yield {"Contents": [{"Key": k, "Size": s} for (k, s) in chunk]}


def _make_destination(
    tmp_path: Path,
    *,
    fail_on: str | None = None,
    objects: list[tuple[str, int]] | None = None,
    region: str = _REGION,
    prefix: str = "platform/backups",
) -> tuple[B2Destination, list[MockB2Client]]:
    """Build a B2Destination wired to a MockB2Client factory.

    Returns the destination + a list the factory appends each built client to, so
    a test can introspect the kwargs boto3 was handed (credentials, endpoint).
    """
    built: list[MockB2Client] = []

    def factory(**kwargs: Any) -> MockB2Client:
        client = MockB2Client(build_kwargs=kwargs, fail_on=fail_on, objects=objects)
        built.append(client)
        return client

    config = B2DestinationConfig(
        bucket="agentic-backups-b2",
        prefix=prefix,
        endpoint_url=b2_endpoint_url(region),
        region=region,
    )
    secrets = StaticSecretsProvider(
        values={
            config.access_key_field: _KEY_ID,
            config.secret_key_field: _APP_KEY,
        }
    )
    dest = B2Destination(config=config, secrets=secrets, client_factory=factory)
    return dest, built


def _bundle(tmp_path: Path, name: str = "20260530T030000Z.tar.enc") -> Path:
    p = tmp_path / name
    p.write_bytes(b"encrypted-bundle-bytes")
    return p


# ---------------------------------------------------------------------------
# Quirk #1 — endpoint derived from region
# ---------------------------------------------------------------------------


def test_b2_endpoint_url_helper_builds_s3_compatible_form() -> None:
    assert b2_endpoint_url("us-west-002") == "https://s3.us-west-002.backblazeb2.com"
    assert b2_endpoint_url("eu-central-003") == "https://s3.eu-central-003.backblazeb2.com"
    # Tolerant of stray slashes/whitespace.
    assert b2_endpoint_url(" us-west-002/ ") == "https://s3.us-west-002.backblazeb2.com"


def test_b2_endpoint_url_helper_rejects_blank_region() -> None:
    with pytest.raises(DestinationError, match="requires a region"):
        b2_endpoint_url("   ")


def test_b2_config_from_settings_derives_endpoint_from_region() -> None:
    class _Settings:
        backup_b2_bucket = "agentic-backups-b2"
        backup_b2_prefix = "platform/backups"
        backup_b2_region = _REGION

    config = B2DestinationConfig.from_b2_settings(_Settings())

    assert config.endpoint_url == _ENDPOINT
    assert config.region == _REGION
    assert config.name == "b2"
    # B2-specific secret-seam field names by default.
    assert config.access_key_field == B2_KEY_ID_FIELD
    assert config.secret_key_field == B2_APPLICATION_KEY_FIELD


def test_upload_forwards_b2_endpoint_and_region_to_client(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    # The B2 S3-compatible endpoint + matching region reach boto3.client.
    assert kwargs["endpoint_url"] == _ENDPOINT
    assert kwargs["region_name"] == _REGION
    assert kwargs["service_name"] == "s3"


# ---------------------------------------------------------------------------
# Quirk #2 — multipart part sizing pinned for B2
# ---------------------------------------------------------------------------


def test_upload_applies_b2_multipart_part_sizing(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    bundle = _bundle(tmp_path)

    result = dest.upload(bundle)

    client = built[0]
    assert len(client.upload_file_calls) == 1
    filename, bucket, key = client.upload_file_calls[0]
    assert filename == str(bundle)
    assert bucket == "agentic-backups-b2"
    assert key == "platform/backups/20260530T030000Z.tar.enc"
    # The TransferConfig carries the B2-friendly part sizing (not AWS defaults).
    config = client.upload_file_config
    assert config is not None
    assert config.multipart_threshold == B2_MULTIPART_THRESHOLD_BYTES
    assert config.multipart_chunksize == B2_MULTIPART_CHUNKSIZE_BYTES
    # Result carries a b2:// URI (distinct scheme) + the real byte size.
    assert result.remote_uri == (
        "b2://agentic-backups-b2/platform/backups/20260530T030000Z.tar.enc"
    )
    assert result.size_bytes == bundle.stat().st_size
    assert result.destination == "b2"


def test_b2_part_size_is_above_the_5mb_floor() -> None:
    # B2's S3-compatible multipart requires every non-final part >= 5 MB; our
    # pinned values must clear that floor with margin.
    five_mb = 5 * 1024 * 1024
    assert five_mb <= B2_MULTIPART_CHUNKSIZE_BYTES
    assert five_mb <= B2_MULTIPART_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# Shared S3 behaviour, reused against B2
# ---------------------------------------------------------------------------


def test_upload_maps_client_error_to_destination_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="upload_file")

    with pytest.raises(DestinationError, match="upload to .* failed"):
        dest.upload(_bundle(tmp_path))


def test_upload_rejects_a_directory_bundle(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path)
    a_dir = tmp_path / "20260530T030000Z"
    a_dir.mkdir()

    with pytest.raises(DestinationError, match="single bundle file"):
        dest.upload(a_dir)


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


def test_download_fetches_prefix_joined_key_to_dest(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)
    out_dir = tmp_path / "restore"
    out_dir.mkdir()

    target = dest.download("20260530T030000Z.tar.enc", out_dir)

    client = built[0]
    assert len(client.download_file_calls) == 1
    bucket, key, filename = client.download_file_calls[0]
    assert bucket == "agentic-backups-b2"
    assert key == "platform/backups/20260530T030000Z.tar.enc"
    assert target == out_dir / "20260530T030000Z.tar.enc"
    assert target.is_file()
    assert filename == str(target)


def test_download_maps_client_error(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="download_file")

    with pytest.raises(DestinationError, match="download of .* failed"):
        dest.download("missing.tar.enc", tmp_path)


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_test_connectivity_ok_does_cheap_head_bucket(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    result = dest.test_connectivity()

    assert isinstance(result, ConnectivityResult)
    assert result.ok is True
    assert built[0].head_bucket_calls == ["agentic-backups-b2"]


def test_test_connectivity_maps_error_without_raising(tmp_path: Path) -> None:
    dest, _ = _make_destination(tmp_path, fail_on="head_bucket")

    result = dest.test_connectivity()

    assert result.ok is False
    assert "head_bucket" in result.detail


def test_test_connectivity_reports_missing_credentials(tmp_path: Path) -> None:
    config = B2DestinationConfig(
        bucket="agentic-backups-b2", endpoint_url=_ENDPOINT, region=_REGION
    )
    dest = B2Destination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        client_factory=lambda **_kw: MockB2Client(build_kwargs={}),
    )

    result = dest.test_connectivity()

    assert result.ok is False
    assert "credentials" in result.detail.lower()


# ---------------------------------------------------------------------------
# Quirk #3 — application-key auth via the B2 secret seam (never plaintext/logged)
# ---------------------------------------------------------------------------


def test_credentials_come_from_b2_secret_seam_and_reach_the_client(tmp_path: Path) -> None:
    dest, built = _make_destination(tmp_path)

    dest.upload(_bundle(tmp_path))

    kwargs = built[0].build_kwargs
    # The B2 application keyId / key map onto S3 access key id / secret.
    assert kwargs["aws_access_key_id"] == _KEY_ID
    assert kwargs["aws_secret_access_key"] == _APP_KEY


def test_missing_credentials_map_to_destination_error_on_upload(tmp_path: Path) -> None:
    config = B2DestinationConfig(
        bucket="agentic-backups-b2", endpoint_url=_ENDPOINT, region=_REGION
    )
    dest = B2Destination(
        config=config,
        secrets=StaticSecretsProvider(values={}),
        client_factory=lambda **_kw: MockB2Client(build_kwargs={}),
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
    assert _KEY_ID not in blob
    assert _APP_KEY not in blob
