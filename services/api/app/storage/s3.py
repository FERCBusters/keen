from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Generator, Tuple
from urllib.parse import parse_qs, quote, urlparse

import boto3
from botocore.config import Config

from app.core.config import settings

_LOCAL_BUCKET = "local://local"


def _validate_key(key: str) -> None:
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(ord(c) < 32 or ord(c) == 127 for c in key)
        or "?" in key
        or "#" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ValueError("Invalid artifact key")


def _local_path(key: str) -> Path:
    _validate_key(key)
    root = Path(settings.artifact_local_dir).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Invalid local artifact path")
    return path


def _require_s3_settings() -> None:
    if settings.s3_use_instance_role:
        if (
            not settings.s3_bucket
            or settings.s3_access_key
            or settings.s3_secret_key
            or settings.s3_endpoint_url
        ):
            raise ValueError(
                "Instance-role S3 requires a bucket and no static credentials/custom endpoint"
            )
        return
    missing = [
        name
        for name, value in (
            ("KEEN_S3_ENDPOINT_URL", settings.s3_endpoint_url),
            ("KEEN_S3_ACCESS_KEY", settings.s3_access_key),
            ("KEEN_S3_SECRET_KEY", settings.s3_secret_key),
            ("KEEN_S3_BUCKET", settings.s3_bucket),
        )
        if not value.strip()
    ]
    if missing:
        raise ValueError("S3 storage requires " + ", ".join(missing))


def _write_backend() -> str:
    backend = settings.artifact_storage_backend
    if backend == "auto":
        configured = (
            settings.s3_endpoint_url,
            settings.s3_access_key,
            settings.s3_secret_key,
            settings.s3_bucket,
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


def _client(*, cleanup=False):
    _require_s3_settings()
    kwargs = {
        "endpoint_url": settings.s3_endpoint_url,
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
        "use_ssl": settings.s3_use_ssl,
    }
    if settings.s3_use_instance_role:
        kwargs = {"region_name": settings.s3_region, "use_ssl": True}
    config = None if settings.s3_use_instance_role else _compat_config()
    if cleanup:
        bounded = Config(
            connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}
        )
        config = config.merge(bounded) if config else bounded
    if config is not None:
        kwargs["config"] = config
    return boto3.client("s3", **kwargs)


_CHUNK = 1024 * 1024


def put_bytes(key: str, data: bytes, content_type: str) -> StoredObject:
    """Store an immutable blob and return its stable URI and checksum."""
    _validate_key(key)
    return _store(key, data, content_type, hashlib.sha256(data).hexdigest(), len(data))


def attempt_key(key: str) -> str:
    """Give an object key a suffix unique to this storage attempt.

    Local and hosted storage refuse to replace an object and other S3 storage
    overwrites it, so two writes under one key collide: a retry after a rolled
    back transaction, or two requests at once. A fresh suffix keeps each
    attempt's object separate and leaves the extension in place.
    """
    folder, _, name = key.rpartition("/")
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        stem, extension = name, ""
    token = uuid.uuid4().hex[:16]
    unique = f"{stem}-{token}" + (f".{extension}" if extension else "")
    return f"{folder}/{unique}" if folder else unique


def put_file(key: str, content: BinaryIO, content_type: str) -> StoredObject:
    """Store a seekable file, reading it in bounded chunks rather than at once."""
    _validate_key(key)
    digest, size = hashlib.sha256(), 0
    content.seek(0)
    for chunk in iter(lambda: content.read(_CHUNK), b""):
        digest.update(chunk)
        size += len(chunk)
    content.seek(0)
    return _store(key, content, content_type, digest.hexdigest(), size)


def _store(
    key: str, body: bytes | BinaryIO, content_type: str, sha: str, size: int
) -> StoredObject:
    backend = _write_backend()
    if backend == "local":
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                if isinstance(body, bytes):
                    output.write(body)
                else:
                    shutil.copyfileobj(body, output, _CHUNK)
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, path)  # never replace an existing evidence object
            return StoredObject(
                uri=f"{_LOCAL_BUCKET}/{key}", sha256=sha, size_bytes=size
            )
        finally:
            os.unlink(temporary)

    if backend != "s3":
        raise ValueError("Unsupported artifact storage backend")

    c = _client()
    result = c.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        **({"IfNoneMatch": "*"} if settings.hosted_mode else {}),
    )
    uri = f"s3://{settings.s3_bucket}/{key}"
    version = result.get("VersionId")
    if settings.hosted_mode and (not version or version == "null"):
        raise RuntimeError("Hosted artifacts require versioned S3 storage")
    if version:
        uri = f"s3://{settings.s3_bucket}/{quote(key, safe='/')}?versionId=" + quote(
            version, safe=""
        )
    return StoredObject(uri=uri, sha256=sha, size_bytes=size)


class ArtifactKey(str):
    """An object key that also carries the S3 version its stored URI selects.

    As a string it is the object key, so callers hand it straight to the read
    and presign helpers, which use the version when one is present.
    """

    version_id: str | None

    def __new__(cls, key, version_id=None):
        obj = super().__new__(cls, key)
        obj.version_id = version_id
        return obj


def _guard_bucket(bucket):
    if settings.hosted_mode and bucket != settings.s3_bucket:
        raise ValueError("Artifact bucket is outside this workspace")


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
    else:
        _guard_bucket(bucket)
        # URIs from a versioned bucket pin the object version; URIs from an
        # unversioned bucket name the key alone.
        if p.query:
            from urllib.parse import unquote

            query = parse_qs(p.query, strict_parsing=True)
            if set(query) != {"versionId"} or len(query["versionId"]) != 1:
                raise ValueError("Invalid S3 artifact version")
            key = ArtifactKey(unquote(key), query["versionId"][0])
    return bucket, key


def get_object_stream(bucket: str, key: str):
    """Return a streaming body for either storage backend."""
    if bucket == _LOCAL_BUCKET:
        return {"Body": _local_path(key).open("rb")}
    _guard_bucket(bucket)
    c = _client()
    version = getattr(key, "version_id", None)
    return c.get_object(
        Bucket=bucket, Key=str(key), **({"VersionId": version} if version else {})
    )


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
    _guard_bucket(bucket)
    c = _client()
    version = getattr(key, "version_id", None)
    return c.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": str(key),
            **({"VersionId": version} if version else {}),
        },
        ExpiresIn=expires_in,
    )


class UnpinnedVersionedObject(ValueError):
    """A versioned bucket holds this URI's object, and the URI names no version."""


def delete_stored_object(uri: str) -> None:
    """Delete one stored object permanently, without bypassing retention locks.

    Version-pinned S3 URIs remove that exact version. A URI stored before its
    bucket kept versions names no version, and deleting it would only add a
    delete marker that hides the evidence while it keeps consuming storage, so
    that deletion is refused.
    """
    bucket, key = parse_s3_uri(uri)
    if bucket == _LOCAL_BUCKET:
        if not Path(settings.artifact_local_dir).is_dir():
            raise FileNotFoundError("Artifact storage root is not mounted")
        _local_path(key).unlink(missing_ok=True)
        return
    if bucket != settings.s3_bucket:
        raise ValueError("Cleanup bucket differs from the configured artifact bucket")
    client = _client(cleanup=True)
    version = getattr(key, "version_id", None)
    if not version:
        if client.get_bucket_versioning(Bucket=bucket).get("Status") in {
            "Enabled",
            "Suspended",
        }:
            raise UnpinnedVersionedObject(
                "Versioned artifact requires a pinned version ID"
            )
    client.delete_object(
        Bucket=bucket, Key=str(key), **({"VersionId": version} if version else {})
    )
