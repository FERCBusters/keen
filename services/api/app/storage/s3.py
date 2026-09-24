from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from app.core.config import settings

_LOCAL_BUCKET = "local://local"


def _local_path(key: str) -> Path:
    root = Path(settings.artifact_local_dir).resolve()
    if not key or key.startswith("/") or "\\" in key or any(
        part in {"", ".", ".."} for part in key.split("/")
    ):
        raise ValueError("Invalid local artifact key")
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Invalid local artifact path")
    return path


def _require_s3_settings() -> None:
    missing = [
        name for name, value in (
            ("KEEN_S3_ENDPOINT_URL", settings.s3_endpoint_url),
            ("KEEN_S3_ACCESS_KEY", settings.s3_access_key),
            ("KEEN_S3_SECRET_KEY", settings.s3_secret_key),
            ("KEEN_S3_BUCKET", settings.s3_bucket),
        ) if not value.strip()
    ]
    if missing:
        raise ValueError("S3 storage requires " + ", ".join(missing))


def _write_backend() -> str:
    backend = settings.artifact_storage_backend
    if backend == "auto":
        configured = (
            settings.s3_endpoint_url, settings.s3_access_key,
            settings.s3_secret_key, settings.s3_bucket,
        )
        if any(value.strip() for value in configured):
            _require_s3_settings()
            return "s3"
        return "local"
    return backend


@dataclass(frozen=True)
class StoredObject:
    uri: str
    sha256: str
    size_bytes: int


def _is_aws_s3_endpoint(endpoint_url: str) -> bool:
    """Return True when the configured endpoint is an AWS-managed endpoint."""
    host = (urlparse(endpoint_url).hostname or "").lower().rstrip(".")
    return host.endswith(".amazonaws.com") or host.endswith(".amazonaws.com.cn")


def _compat_config() -> Config | None:
    """Return an S3 client config that preserves AWS defaults where possible.

    Newer boto3/botocore versions enable streaming trailer checksums for S3
    uploads. AWS S3 supports this, but some S3-compatible providers, including
    Linode Object Storage, reject those requests. In auto mode, keep the AWS
    defaults for AWS endpoints and enable compatibility settings elsewhere.
    """
    mode = settings.s3_compatibility_mode
    if mode == "aws" or (
        mode == "auto" and _is_aws_s3_endpoint(settings.s3_endpoint_url)
    ):
        return None

    return Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )


def _client():
    _require_s3_settings()
    kwargs = {
        "endpoint_url": settings.s3_endpoint_url,
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
        "use_ssl": settings.s3_use_ssl,
    }
    config = _compat_config()
    if config is not None:
        kwargs["config"] = config
    return boto3.client("s3", **kwargs)


def put_bytes(key: str, data: bytes, content_type: str) -> StoredObject:
    """Store an immutable blob and return its stable URI and checksum."""
    sha = hashlib.sha256(data).hexdigest()
    size = len(data)

    backend = _write_backend()
    if backend == "local":
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, path)  # never replace an existing evidence object
            return StoredObject(uri=f"{_LOCAL_BUCKET}/{key}", sha256=sha, size_bytes=size)
        finally:
            os.unlink(temporary)

    if backend != "s3":
        raise ValueError("Unsupported artifact storage backend")

    c = _client()
    c.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    uri = f"s3://{settings.s3_bucket}/{key}"
    return StoredObject(uri=uri, sha256=sha, size_bytes=size)


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """Parse an artifact URI into (bucket, key), including local storage."""
    p = urlparse(uri)
    if p.scheme not in {"s3", "local"} or not p.netloc:
        raise ValueError(f"Unsupported artifact URI: {uri}")
    bucket = p.netloc
    key = p.path.lstrip("/")
    if not key:
        raise ValueError(f"Missing object key in artifact URI: {uri}")
    if p.scheme == "local":
        if bucket != "local" or p.query or p.fragment:
            raise ValueError("Invalid local artifact URI")
        _local_path(key)
        # Keep the existing S3 (bucket, key) return contract intact.
        bucket = _LOCAL_BUCKET
    return bucket, key


def get_object_stream(bucket: str, key: str):
    """Return a streaming body for either storage backend."""
    if bucket == _LOCAL_BUCKET:
        return {"Body": _local_path(key).open("rb")}
    c = _client()
    return c.get_object(Bucket=bucket, Key=key)


def iter_stream(body, chunk_size: int = 1024 * 1024) -> Generator[bytes, None, None]:
    """Yield bytes and close the underlying local file or S3 body."""
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        body.close()


def presign_get_url(bucket: str, key: str, expires_in: int = 900) -> str:
    """Optionally generate a presigned GET URL (not required by the UI)."""
    c = _client()
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )
