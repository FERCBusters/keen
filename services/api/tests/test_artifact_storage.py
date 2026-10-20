"""Evidence artifact storage: immutability, containment, version pinning and isolation.

Local storage runs against a real temporary directory. S3 runs against a fake
boto3 client that records exactly what KEEN asks the bucket to do.
"""

import hashlib
import io
import os
import stat
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.storage import s3


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(root))
    monkeypatch.setattr(settings, "hosted_mode", False)
    return SimpleNamespace(root=root, outside=tmp_path)


class FakeBucket:
    def __init__(self, version="v/1"):
        self.version = version
        self.calls = []
        self.objects = {}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        self.objects[(kwargs["Key"], self.version)] = kwargs["Body"]
        return {"VersionId": self.version} if self.version else {}

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {
            "Body": io.BytesIO(self.objects[(kwargs["Key"], kwargs.get("VersionId"))])
        }

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append((operation, Params))
        return "https://signed.example/object"

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket()
    clients = []

    def client(service, **kwargs):
        clients.append((service, kwargs))
        return fake

    monkeypatch.setattr(s3.boto3, "client", client)
    for name, value in {
        "artifact_storage_backend": "s3",
        "s3_use_instance_role": False,
        "s3_endpoint_url": "https://objects.example",
        "s3_access_key": "synthetic-access",
        "s3_secret_key": "synthetic-secret",
        "s3_bucket": "evidence",
        "s3_region": "ap-southeast-2",
        "s3_use_ssl": True,
        "s3_compatibility_mode": "auto",
        "hosted_mode": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    fake.clients = clients
    return fake


# --- Local storage -------------------------------------------------------------


def test_local_artifacts_round_trip_privately(local):
    stored = s3.put_bytes("events/2026/a.json", b'{"ok": true}', "application/json")

    assert stored.uri == "local://local/events/2026/a.json"
    assert stored.size_bytes == 12
    assert stored.sha256 == (
        "6bc0da1f42f96fc37b8bd7ed20ba57606d2a0da5cda2b135c7854fbdc985b8a3"
    )
    path = local.root / "events/2026/a.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    bucket, key = s3.parse_s3_uri(stored.uri)
    body = s3.get_object_stream(bucket, key)["Body"]
    assert b"".join(s3.iter_stream(body, chunk_size=4)) == b'{"ok": true}'
    assert body.closed


class Recording(io.BytesIO):
    """A seekable upload that records how much each read asked for."""

    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read(self, size=-1):
        self.reads.append(size)
        return super().read(size)


def test_files_are_stored_in_bounded_chunks(local):
    data = os.urandom(3 * 1024 * 1024 + 5)
    content = Recording(data)
    content.seek(0, os.SEEK_END)  # wherever the caller left it

    stored = s3.put_file("isms-documents/d/report.pdf", content, "application/pdf")

    assert (stored.uri, stored.size_bytes) == (
        "local://local/isms-documents/d/report.pdf",
        len(data),
    )
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    path = local.root / "isms-documents/d/report.pdf"
    assert path.read_bytes() == data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Each read takes at most one MiB; nothing reads the whole file at once.
    assert content.reads and all(0 < size <= 1024 * 1024 for size in content.reads)
    with pytest.raises(FileExistsError):
        s3.put_file("isms-documents/d/report.pdf", Recording(b"forged"), "text/plain")
    assert path.read_bytes() == data


def test_stored_evidence_is_never_replaced(local):
    s3.put_bytes("events/a.json", b"original", "text/plain")

    with pytest.raises(FileExistsError):
        s3.put_bytes("events/a.json", b"forged", "text/plain")

    assert (local.root / "events/a.json").read_bytes() == b"original"
    # No partially written temporary files are left behind.
    assert sorted(p.name for p in (local.root / "events").iterdir()) == ["a.json"]


def test_a_symlink_cannot_carry_writes_or_reads_outside_the_root(local):
    secret = local.outside / "secret.txt"
    secret.write_bytes(b"outside the artifact root")
    os.symlink(local.outside, local.root / "escape")
    os.symlink(local.root, local.root / "loop")

    for key in ("escape/secret.txt", "escape/new.txt", "loop"):
        with pytest.raises(ValueError, match="Invalid local artifact path"):
            s3.put_bytes(key, b"x", "text/plain")
        with pytest.raises(ValueError, match="Invalid local artifact path"):
            s3.parse_s3_uri(f"local://local/{key}")
        with pytest.raises(ValueError, match="Invalid local artifact path"):
            s3.get_object_stream("local://local", key)
    assert not (local.outside / "new.txt").exists()
    assert secret.read_bytes() == b"outside the artifact root"


@pytest.mark.parametrize(
    "key",
    [
        "/etc/passwd",
        "events/../../secret",
        "events/./a.json",
        "events//a.json",
        "events\\a.json",
        "events/\x00a.json",
        "events/\x7fa.json",
        "events/a?query.json",
        "events/a#fragment.json",
    ],
)
def test_hostile_keys_are_rejected_before_any_storage_work(local, key):
    # Traversal, absolute, escaped and control-character keys never reach the
    # filesystem: no file, directory or symlink is created anywhere.
    with pytest.raises(ValueError, match="Invalid artifact key"):
        s3.put_bytes(key, b"x", "text/plain")
    assert list(local.root.rglob("*")) == []


def test_deleting_from_an_unmounted_root_is_reported(local):
    stored = s3.put_bytes("events/a.json", b"x", "text/plain")
    os.unlink(local.root / "events/a.json")
    os.rmdir(local.root / "events")
    os.rmdir(local.root)

    # Retention must not record a purge that never reached the storage volume.
    with pytest.raises(FileNotFoundError, match="Artifact storage root is not mounted"):
        s3.delete_stored_object(stored.uri)


@pytest.mark.parametrize(
    "uri,message",
    [
        ("file:///etc/passwd", "Unsupported artifact URI"),
        ("s3:///no-bucket", "Unsupported artifact URI"),
        ("s3://evidence/", "Missing object key"),
        ("local://elsewhere/events/a", "Invalid local artifact URI"),
        ("local://local/events/a?versionId=1", "Invalid local artifact URI"),
        ("local://local/events/a#fragment", "Invalid local artifact URI"),
        ("local://local/events/../../secret", "Invalid artifact key"),
        ("s3://evidence/a?versionId=1&extra=2", "Invalid S3 artifact version"),
        ("s3://evidence/a?versionId=1&versionId=2", "Invalid S3 artifact version"),
        ("s3://evidence/a?other=1", "Invalid S3 artifact version"),
    ],
)
def test_artifact_uris_are_parsed_strictly(local, uri, message):
    with pytest.raises(ValueError, match=message):
        s3.parse_s3_uri(uri)


# --- S3 ------------------------------------------------------------------------


def test_s3_artifacts_are_pinned_to_the_version_written(bucket):
    stored = s3.put_bytes("events/a b.json", b"evidence", "application/json")

    ((name, put),) = bucket.calls
    assert name == "put_object"
    assert put == {
        "Bucket": "evidence",
        "Key": "events/a b.json",
        "Body": b"evidence",
        "ContentType": "application/json",
    }
    assert stored.uri == "s3://evidence/events/a%20b.json?versionId=v%2F1"

    location, key = s3.parse_s3_uri(stored.uri)
    assert (location, str(key), key.version_id) == (
        "evidence",
        "events/a b.json",
        "v/1",
    )
    body = s3.get_object_stream(location, key)["Body"]
    assert body.read() == b"evidence"
    assert bucket.calls[-1] == (
        "get_object",
        {"Bucket": "evidence", "Key": "events/a b.json", "VersionId": "v/1"},
    )
    s3.presign_get_url(location, key)
    assert bucket.calls[-1][1]["VersionId"] == "v/1"


def test_s3_receives_a_file_as_a_stream_from_its_start(bucket):
    content = Recording(b"evidence")
    content.seek(3)

    stored = s3.put_file("isms-documents/a b.pdf", content, "application/pdf")

    ((name, put),) = bucket.calls
    assert name == "put_object"
    assert put["Body"] is content and content.tell() == 0
    assert (put["Key"], put["ContentType"]) == (
        "isms-documents/a b.pdf",
        "application/pdf",
    )
    assert (stored.sha256, stored.size_bytes) == (
        hashlib.sha256(b"evidence").hexdigest(),
        8,
    )
    assert stored.uri == "s3://evidence/isms-documents/a%20b.pdf?versionId=v%2F1"


def test_an_unversioned_bucket_stores_artifacts_under_a_plain_uri(bucket):
    bucket.version = None
    stored = s3.put_bytes("events/a b.json", b"evidence", "application/json")

    assert stored.uri == "s3://evidence/events/a b.json"
    location, key = s3.parse_s3_uri(stored.uri)
    assert (location, key) == ("evidence", "events/a b.json")
    assert getattr(key, "version_id", None) is None


@pytest.mark.parametrize("version", [None, "null"])
def test_hosted_storage_requires_versioned_write_once_objects(
    bucket, monkeypatch, version
):
    monkeypatch.setattr(settings, "hosted_mode", True)
    bucket.version = version

    with pytest.raises(RuntimeError, match="Hosted artifacts require versioned S3"):
        s3.put_bytes("events/a.json", b"evidence", "application/json")
    # The write itself refused to replace an existing object.
    assert bucket.calls[0][1]["IfNoneMatch"] == "*"


def test_hosted_storage_reads_only_its_own_bucket(bucket, monkeypatch):
    monkeypatch.setattr(settings, "hosted_mode", True)

    for operation in (
        lambda: s3.parse_s3_uri("s3://another-tenant/events/a.json?versionId=1"),
        lambda: s3.get_object_stream("another-tenant", "events/a.json"),
        lambda: s3.presign_get_url("another-tenant", "events/a.json"),
    ):
        with pytest.raises(ValueError, match="outside this workspace"):
            operation()
    assert bucket.calls == [] and bucket.clients == []
    assert s3.parse_s3_uri("s3://evidence/events/a.json")[0] == "evidence"


def test_the_s3_client_uses_the_configured_credentials_and_endpoint(bucket):
    s3.put_bytes("events/a.json", b"x", "text/plain")

    ((service, kwargs),) = bucket.clients
    assert service == "s3"
    config = kwargs.pop("config")
    assert kwargs == {
        "endpoint_url": "https://objects.example",
        "aws_access_key_id": "synthetic-access",
        "aws_secret_access_key": "synthetic-secret",
        "region_name": "ap-southeast-2",
        "use_ssl": True,
    }
    # S3-compatible services reject streaming trailer checksums.
    assert config.request_checksum_calculation == "when_required"
    assert config.s3 == {"addressing_style": "path"}


@pytest.mark.parametrize(
    "endpoint,mode,compatible",
    [
        ("https://s3.ap-southeast-2.amazonaws.com", "auto", False),
        ("https://bucket.s3.cn-north-1.amazonaws.com.cn", "auto", False),
        ("https://amazonaws.com.attacker.example", "auto", True),
        ("https://objects.example", "aws", False),
        ("https://s3.ap-southeast-2.amazonaws.com", "s3_compatible", True),
    ],
)
def test_compatibility_mode_follows_the_endpoint(
    bucket, monkeypatch, endpoint, mode, compatible
):
    monkeypatch.setattr(settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(settings, "s3_compatibility_mode", mode)

    s3.put_bytes("events/a.json", b"x", "text/plain")

    ((_, kwargs),) = bucket.clients
    assert ("config" in kwargs) is compatible


def test_cleanup_removes_exactly_the_pinned_version_and_gives_up_quickly(bucket):
    s3.delete_stored_object("s3://evidence/events/a%20b.json?versionId=v%2F1")

    assert bucket.calls == [
        (
            "delete_object",
            {"Bucket": "evidence", "Key": "events/a b.json", "VersionId": "v/1"},
        )
    ]
    ((_, kwargs),) = bucket.clients
    config = kwargs["config"]
    assert (config.connect_timeout, config.read_timeout) == (5, 15)
    assert config.retries == {"max_attempts": 1}


STATIC_S3_SETTINGS = ("s3_endpoint_url", "s3_access_key", "s3_secret_key")


@pytest.mark.parametrize("kept", STATIC_S3_SETTINGS)
def test_instance_roles_never_mix_with_static_credentials(bucket, monkeypatch, kept):
    monkeypatch.setattr(settings, "s3_use_instance_role", True)
    for name in STATIC_S3_SETTINGS:
        if name != kept:
            monkeypatch.setattr(settings, name, "")

    with pytest.raises(
        ValueError, match="Instance-role S3 requires a bucket and no static"
    ):
        s3.put_bytes("events/a.json", b"x", "text/plain")
    assert bucket.calls == [] and bucket.clients == []


def test_instance_roles_send_only_the_region(bucket, monkeypatch):
    monkeypatch.setattr(settings, "s3_use_instance_role", True)
    for name in STATIC_S3_SETTINGS:
        monkeypatch.setattr(settings, name, "")

    s3.put_bytes("events/a.json", b"x", "text/plain")
    ((_, kwargs),) = bucket.clients
    assert kwargs == {"region_name": "ap-southeast-2", "use_ssl": True}


def test_partial_s3_settings_never_fall_back_to_local_disk(local, monkeypatch):
    # A half-finished S3 configuration must not silently keep evidence on a
    # container's disk.
    monkeypatch.setattr(settings, "artifact_storage_backend", "auto")
    for name, value in {
        "s3_use_instance_role": False,
        "s3_endpoint_url": "https://objects.example",
        "s3_access_key": "",
        "s3_secret_key": "",
        "s3_bucket": "evidence",
    }.items():
        monkeypatch.setattr(settings, name, value)

    with pytest.raises(
        ValueError, match="S3 storage requires KEEN_S3_ACCESS_KEY, KEEN_S3_SECRET_KEY"
    ):
        s3.put_bytes("events/a.json", b"x", "text/plain")
    assert list(local.root.iterdir()) == []

    monkeypatch.setattr(settings, "s3_endpoint_url", "")
    monkeypatch.setattr(settings, "s3_bucket", "")
    assert s3.put_bytes("events/a.json", b"x", "text/plain").uri.startswith("local://")


def test_an_unknown_backend_stores_nothing(local, monkeypatch):
    monkeypatch.setattr(settings, "artifact_storage_backend", "ftp")
    with pytest.raises(ValueError, match="Unsupported artifact storage backend"):
        s3.put_bytes("events/a.json", b"x", "text/plain")
    assert list(local.root.iterdir()) == []
