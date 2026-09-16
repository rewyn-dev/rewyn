"""S3-compatible object storage (spec §51).

Event logs are the bulk of what a platform stores, and past a certain volume
they do not belong on a disk you have to back up. This implements the
object-store interface — ``put``, ``get``, ``exists``, ``delete`` — against
S3, so swapping storage is a constructor argument rather than a migration.

Works with anything speaking the S3 API: AWS, MinIO, Cloudflare R2, Ceph.
Point ``endpoint_url`` at the one you use::

    pip install "rewyn[s3]"
"""

from __future__ import annotations

from typing import Any

from rewyn.core.types import MissingDependencyError


def _client(
    *,
    client: Any = None,
    region: str | None = None,
    endpoint_url: str | None = None,
    **credentials: Any,
) -> Any:
    if client is not None:
        return client
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise MissingDependencyError("boto3", "s3") from exc
    return boto3.client("s3", region_name=region, endpoint_url=endpoint_url, **credentials)


class S3ObjectStore:
    """Blobs in an S3 bucket, keyed exactly as the local store keys them."""

    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        client: Any = None,
        region: str | None = None,
        endpoint_url: str | None = None,
        **credentials: Any,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = _client(
            client=client, region=region, endpoint_url=endpoint_url, **credentials
        )

    def __repr__(self) -> str:
        return f"S3ObjectStore({self.bucket!r}, prefix={self.prefix!r})"

    def _key(self, key: str) -> str:
        clean = key.strip("/").replace("..", "_")
        return f"{self.prefix}/{clean}" if self.prefix else clean

    # ObjectStore --------------------------------------------------------------
    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)
        return key

    def get(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        body: bytes = response["Body"].read()
        return body

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise
        return True


def _is_missing(exc: Exception) -> bool:
    """True for "this key is not here", false for "we could not ask".

    Treating a permissions failure as a missing object would turn a
    misconfiguration into silent data loss, so the two are distinguished by
    the error code rather than by catching everything.
    """
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return str(code) in {"404", "NoSuchKey", "NotFound"} or type(exc).__name__ in {
        "NoSuchKey",
        "404",
    }
