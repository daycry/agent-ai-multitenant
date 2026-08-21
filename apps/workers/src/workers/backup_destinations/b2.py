"""Destino Backblaze B2 (task_12_06) — S3-compatible, con tres rarezas.

**B2 HABLA S3**, así que este módulo REUTILIZA el adaptador S3 entero por
herencia en vez de duplicar upload/list/download/connectivity. Las tres rarezas
que sí cambian están documentadas en el bloque de abajo. Romper esa herencia
"desduplicando" sería cambiar el camino de subida a B2 sin tocar una sola firma;
lo vigila ``tests/unit/test_backup_destinations_package.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workers.backup_destinations.base import DestinationError
from workers.backup_destinations.s3 import S3Destination, S3DestinationConfig

# ---------------------------------------------------------------------------
# Backblaze B2 destination (task_12_06) — S3-compatible, with quirks.
# ---------------------------------------------------------------------------
#
# B2's S3-compatible API IS S3, so we reuse the whole S3 adapter rather than
# duplicate upload/list/download/connectivity. What differs are three quirks the
# B2 layer handles:
#
#   1. ENDPOINT — B2 exposes its S3 API at ``s3.<region>.backblazeb2.com`` (e.g.
#      ``us-west-002`` → ``https://s3.us-west-002.backblazeb2.com``). The operator
#      configures only the REGION; the endpoint is derived. region_name is also
#      set so boto3's SigV4 signing uses the matching region.
#   2. MULTIPART PART SIZE — B2's S3-compatible multipart requires every part
#      except the last to be at least 5 MB, and large objects upload far more
#      reliably with bigger parts. boto3's default 8 MiB chunk is acceptable but
#      we pin an explicit, B2-friendly part size + threshold via a TransferConfig
#      so the behaviour is deterministic and not at the mercy of an SDK default.
#   3. AUTH — B2's application keyId / applicationKey map straight onto S3's
#      access_key_id / secret_access_key, but they are DISTINCT secrets, so the
#      B2 destination resolves them from their own secret-seam field names.

# B2's S3-compatible multipart minimum part size is 5 MB; we use 100 MB parts
# (well above the floor) so very large encrypted bundles upload in sane chunks.
# boto3 switches to multipart once a file exceeds the threshold; below it a
# single PUT is used. Both are part of the deterministic B2 tuning.
B2_MULTIPART_THRESHOLD_BYTES = 100 * 1024 * 1024
B2_MULTIPART_CHUNKSIZE_BYTES = 100 * 1024 * 1024

# The secret-seam field names the B2 adapter resolves. DISTINCT from the S3 ones
# (a deployment may use both): B2 application keyId + applicationKey.
B2_KEY_ID_FIELD = "backup_b2_key_id"
B2_APPLICATION_KEY_FIELD = "backup_b2_application_key"


def b2_endpoint_url(region: str) -> str:
    """Derive B2's S3-compatible endpoint URL from a region.

    ``us-west-002`` → ``https://s3.us-west-002.backblazeb2.com``. This is the
    documented B2 S3 endpoint form; the region also drives SigV4 signing.
    """
    r = region.strip().strip("/")
    if not r:
        raise DestinationError("B2 destination requires a region (e.g. 'us-west-002')")
    return f"https://s3.{r}.backblazeb2.com"


@dataclass(frozen=True)
class B2DestinationConfig(S3DestinationConfig):
    """NON-secret config for a Backblaze B2 destination.

    Reuses :class:`S3DestinationConfig` (B2 IS S3) but the operator configures a
    REGION, not an endpoint — the S3-compatible endpoint is derived from it. The
    application keyId / key are NOT here (they are secrets); the field names
    default to the B2-specific secret-seam keys.
    """

    name: str = "b2"
    access_key_field: str = B2_KEY_ID_FIELD
    secret_key_field: str = B2_APPLICATION_KEY_FIELD

    @classmethod
    def from_b2_settings(cls, settings: Any) -> B2DestinationConfig:
        """Build the B2 destination config from the workers :class:`Settings`.

        Reads only the NON-secret tunables (bucket, prefix, region); the
        application key id + key stay in the secret seam. The endpoint is derived
        from the region (B2 quirk #1) and ``region`` is preserved so SigV4 signing
        matches.
        """
        region = str(settings.backup_b2_region)
        return cls(
            bucket=str(settings.backup_b2_bucket),
            prefix=str(settings.backup_b2_prefix),
            endpoint_url=b2_endpoint_url(region),
            region=region,
        )

    def uri_for(self, key: str) -> str:
        """A human ``b2://bucket/key`` URI for logs/results (distinct from s3://)."""
        return f"b2://{self.bucket}/{key}"


@dataclass
class B2Destination(S3Destination):
    """Upload a backup bundle to Backblaze B2 via its S3-compatible API.

    Subclasses :class:`S3Destination` — the upload/list/download/connectivity
    behaviour is identical because B2 speaks S3. Only the quirks are layered on:

      * the endpoint is the B2 S3 endpoint derived from the region (handled by
        :class:`B2DestinationConfig`, forwarded to boto3 unchanged);
      * multipart part sizing is pinned to B2-friendly values via a
        ``TransferConfig`` (overridden :meth:`_upload_kwargs`);
      * credentials are the B2 application keyId / key, resolved from the
        B2-specific secret-seam field names (again via the config).

    Credentials are resolved through the secret seam at client-build time exactly
    as the S3 adapter does — never plaintext, never logged.
    """

    config: B2DestinationConfig

    def _upload_kwargs(self) -> dict[str, Any]:
        """Pin B2-friendly multipart sizing (quirk #2) via a boto3 TransferConfig.

        Imported lazily so boto3 is only required in production / when uploading
        for real; tests inject a mock client and assert the ``Config`` it received
        carries the B2 part sizing.
        """
        from boto3.s3.transfer import TransferConfig

        return {
            "Config": TransferConfig(
                multipart_threshold=B2_MULTIPART_THRESHOLD_BYTES,
                multipart_chunksize=B2_MULTIPART_CHUNKSIZE_BYTES,
            )
        }
