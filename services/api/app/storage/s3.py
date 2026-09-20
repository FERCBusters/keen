from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Generator, Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from app.core.config import settings


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
    """Store a blob in the configured S3 bucket and return a stable URI + checksum."""
    sha = hashlib.sha256(data).hexdigest()
    size = len(data)

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
    """Parse s3://bucket/key into (bucket, key)."""
    p = urlparse(uri)
    if p.scheme != "s3" or not p.netloc:
        raise ValueError(f"Unsupported S3 URI: {uri}")
    bucket = p.netloc
    key = p.path.lstrip("/")
    if not key:
        raise ValueError(f"Missing object key in S3 URI: {uri}")
    return bucket, key


def get_object_stream(bucket: str, key: str):
    """Return the boto3 streaming body for an object."""
    c = _client()
    return c.get_object(Bucket=bucket, Key=key)


def iter_stream(body, chunk_size: int = 1024 * 1024) -> Generator[bytes, None, None]:
    """Yield bytes from a boto3 StreamingBody."""
    while True:
        chunk = body.read(chunk_size)
        if not chunk:
            break
        yield chunk


def presign_get_url(bucket: str, key: str, expires_in: int = 900) -> str:
    """Optionally generate a presigned GET URL (not required by the UI)."""
    c = _client()
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )
