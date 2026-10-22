"""Digest pins for third-party container images.

KEEN pins every third-party image it builds from or runs, and
tools/docker_image_pins.py keeps those pins current. These tests cover how the
tool reads references, verifies registry responses and rewrites files, and
hold the repository's own images and workflows to the same policy.
"""

import hashlib
import http.server
import importlib.util
import io
import json
import re
import ssl
import sys
import threading
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
VERSION = "org.opencontainers.image.version"
CREATED = "org.opencontainers.image.created"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "docker_image_pins", ROOT / "tools" / "docker_image_pins.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pins = _load_tool()
Response = pins.Response


def sha(character: str) -> str:
    return "sha256:" + character * 64


class FakeHub:
    """Docker Hub's registry and token service, serving fixed image indexes."""

    def __init__(self):
        self.tags = {}
        self.manifests = {}
        self.calls = []  # every request: (method, host, path, query, headers)
        self.served = []  # authorised manifest requests: (method, path)
        self.answers = {}  # (method, path) -> Response, replacing normal service
        self._serial = 0

    def publish(
        self,
        repository,
        tag,
        *,
        platforms=pins.RELEASE_PLATFORMS,
        version=None,
        created=None,
        media_type=pins.OCI_INDEX,
        body=None,
    ):
        """Point a tag at a new index and return the index digest."""
        if body is None:
            self._serial += 1
            annotations = {VERSION: version, CREATED: created}
            entries = [
                {
                    "mediaType": OCI_MANIFEST,
                    "digest": "sha256:"
                    + hashlib.sha256(
                        f"{tag}{self._serial}{platform}".encode()
                    ).hexdigest(),
                    "size": 1000,
                    "platform": dict(zip(("os", "architecture"), platform.split("/"))),
                    "annotations": {k: v for k, v in annotations.items() if v},
                }
                for platform in platforms
            ]
            # Official images also list attestation manifests.
            entries.append(
                {
                    "mediaType": OCI_MANIFEST,
                    "digest": sha("e"),
                    "size": 1,
                    "platform": {"os": "unknown", "architecture": "unknown"},
                }
            )
            body = json.dumps(
                {"schemaVersion": 2, "mediaType": media_type, "manifests": entries}
            ).encode()
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        self.manifests[(repository, digest)] = (media_type, body)
        self.tags[(repository, tag)] = digest
        return digest

    def __call__(self, method, url, headers):
        parts = urllib.parse.urlsplit(url)
        self.calls.append(
            (method, parts.netloc, parts.path, parts.query, dict(headers))
        )
        if (method, parts.path) in self.answers:
            return self.answers[(method, parts.path)]
        if parts.netloc == "auth.docker.io":
            scope = dict(urllib.parse.parse_qsl(parts.query))["scope"]
            token = "token-" + scope.split(":")[1].replace("/", "-")
            return Response(200, {}, json.dumps({"token": token}).encode())
        assert parts.netloc == "registry-1.docker.io"
        repository, _, reference = parts.path.removeprefix("/v2/").rpartition(
            "/manifests/"
        )
        if headers.get("Authorization") != "Bearer token-" + repository.replace(
            "/", "-"
        ):
            challenge = (
                'Bearer realm="https://auth.docker.io/token",'
                f'service="registry.docker.io",scope="repository:{repository}:pull"'
            )
            return Response(401, {"www-authenticate": challenge}, b"")
        self.served.append((method, parts.path))
        digest = self.tags.get((repository, reference), reference)
        if (repository, digest) not in self.manifests:
            return Response(404, {}, b"")
        media_type, body = self.manifests[(repository, digest)]
        headers = {"docker-content-digest": digest, "content-type": media_type}
        return Response(200, headers, b"" if method == "HEAD" else body)


def write_tree(root, files):
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run(command, root, hub, *extra):
    out, err = io.StringIO(), io.StringIO()
    code = pins.main(
        [command, "--root", str(root), *extra],
        registry=pins.Registry(hub),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def report(directory):
    lines = (directory / "outputs.txt").read_text().splitlines()
    outputs = dict(line.split("=", 1) for line in lines)
    body = (directory / "pr-body.md").read_text()
    return outputs, body, (directory / "commit-message.txt").read_text()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("postgres:16", ("docker.io", "library/postgres", "16", None)),
        ("docker.io/library/debian:13", ("docker.io", "library/debian", "13", None)),
        (f"valkey/valkey:8@{sha('a')}", ("docker.io", "valkey/valkey", "8", sha("a"))),
        (
            "index.docker.io/node:22-alpine",
            ("docker.io", "library/node", "22-alpine", None),
        ),
        (
            "ghcr.io/fercbusters/keen-api:1.0.0",
            ("ghcr.io", "fercbusters/keen-api", "1.0.0", None),
        ),
        (
            "localhost:5000/tools/builder",
            ("localhost:5000", "tools/builder", None, None),
        ),
    ],
)
def test_references_are_normalised_to_registry_repository_tag_and_digest(
    text, expected
):
    image = pins.parse_image(text)
    assert (image.registry, image.repository, image.tag, image.digest) == expected


@pytest.mark.parametrize(
    "text,message",
    [
        ("postgres:16@sha256:abc", "malformed digest"),
        ("postgres:16@sha256:" + "A" * 64, "malformed digest"),
        ("postgres:16@md5:" + "a" * 32, "malformed digest"),
        ("Postgres:16", "not a valid image name"),
        ("postgres:-16", "malformed tag"),
        ("Registry.Example/team/app:1", "malformed registry"),
    ],
)
def test_malformed_references_are_refused(text, message):
    with pytest.raises(pins.PinError, match=message):
        pins.parse_image(text)


def test_references_are_read_from_dockerfiles_compose_files_and_workflows(tmp_path):
    files = {
        "services/app/Dockerfile": (
            "# syntax=docker/dockerfile:1\n"
            "FROM --platform=$BUILDPLATFORM node:20-alpine AS build\n"
            "RUN --mount=type=bind,from=ghcr.io/example/assets:2,target=/a true\n"
            "FROM build AS test\n"
            "COPY --from=build /out /out\n"
            "COPY --from=docker.io/library/busybox:1.37 /bin/busybox /bin/\n"
            "FROM scratch\n"
            f"FROM nginx:1.30-alpine@{sha('a')}\n"
        ),
        "docker-compose.yml": (
            "services:\n"
            "  db:\n"
            '    image: "postgres:16"  # primary store\n'
            "  api:\n"
            "    image: ${KEEN_API_IMAGE:-ghcr.io/fercbusters/keen-api:1.0.0}\n"
            "  cache:\n"
            "    image: ${CACHE_IMAGE:-valkey/valkey:8}\n"
        ),
        "docker-compose.build.yml": (
            "services:\n  api:\n    image: keen-api:local\n    build: ./services/api\n"
        ),
        ".github/workflows/ci.yml": (
            "jobs:\n"
            "  test:\n"
            "    container: debian:13\n"
            "    services:\n"
            "      db:\n"
            "        image: docker.io/library/postgres:16\n"
            "    steps:\n"
            "      # image: commented/out:1\n"
            "      - uses: docker://alpine:3.22\n"
            f"      - uses: docker/setup-buildx-action@{'0' * 40}\n"
            "        with:\n"
            "          driver-opts: image=moby/buildkit:buildx-stable-1\n"
        ),
        "node_modules/package/Dockerfile": "FROM ignored:1\n",
        "docs/example.yml": "image: ignored:1\n",
    }
    write_tree(tmp_path, files)

    references = pins.find_references(tmp_path)

    assert sorted((r.where, r.text, r.keen) for r in references) == sorted(
        [
            (".github/workflows/ci.yml:3", "debian:13", False),
            (".github/workflows/ci.yml:6", "docker.io/library/postgres:16", False),
            (".github/workflows/ci.yml:9", "alpine:3.22", False),
            (".github/workflows/ci.yml:12", "moby/buildkit:buildx-stable-1", False),
            ("docker-compose.build.yml:3", "keen-api:local", True),
            ("docker-compose.yml:3", "postgres:16", False),
            ("docker-compose.yml:5", "ghcr.io/fercbusters/keen-api:1.0.0", True),
            ("docker-compose.yml:7", "valkey/valkey:8", False),
            ("services/app/Dockerfile:2", "node:20-alpine", False),
            ("services/app/Dockerfile:3", "ghcr.io/example/assets:2", False),
            ("services/app/Dockerfile:6", "docker.io/library/busybox:1.37", False),
            ("services/app/Dockerfile:8", f"nginx:1.30-alpine@{sha('a')}", False),
        ]
    )
    assert not any(r.problem for r in references)
    # Each recorded span is exactly the text an update replaces.
    for ref in references:
        line = files[ref.path].splitlines()[ref.line - 1]
        assert line[ref.start : ref.end] == ref.text


def test_references_that_cannot_be_pinned_stop_an_update(tmp_path):
    write_tree(
        tmp_path,
        {
            "Dockerfile": "ARG BASE=python:3.12-slim\nFROM ${BASE}\nFROM alpine\n",
            "compose.yaml": (
                "services:\n"
                "  a:\n    image: ${IMAGE}\n"
                "  b:\n    image: Postgres:16\n"
                "  c:\n    image: postgres:16@sha256:abc\n"
            ),
            ".github/workflows/ci.yml": (
                "jobs:\n  t:\n    container:\n      image: ${{ matrix.image }}\n"
            ),
        },
    )
    variable = "comes from a variable; write the image out in full to pin it"
    problems = {r.where: r.problem for r in pins.find_references(tmp_path) if r.problem}
    assert problems == {
        "Dockerfile:2": variable,
        "Dockerfile:3": "has no tag; name the tag whose updates it follows",
        "compose.yaml:3": variable,
        "compose.yaml:5": "is not a valid image name",
        "compose.yaml:7": "has a malformed digest; use sha256: and 64 lower-case hex",
        ".github/workflows/ci.yml:4": variable,
    }

    hub, before = FakeHub(), snapshot(tmp_path)
    code, out, err = run("update", tmp_path, hub)
    assert code == 2
    assert "fix the references above" in err
    assert f"problem   Dockerfile:2: ${{BASE}} {variable}" in out
    assert snapshot(tmp_path) == before
    assert hub.calls == []
    # check reports the same references as findings.
    assert run("check", tmp_path, hub)[0] == 1


def test_required_variables_are_left_for_the_deployment_to_supply(tmp_path):
    hub = FakeHub()
    python = hub.publish("library/python", "3.12-slim")
    compose = (
        "services:\n"
        "  db:\n    image: ${POSTGRES_IMAGE:?Set a digest-pinned PostgreSQL 16 image}\n"
        "  app:\n    image: '${APP_IMAGE?set APP_IMAGE}'\n"
    )
    write_tree(
        tmp_path, {"Dockerfile": "FROM python:3.12-slim\n", "compose.yml": compose}
    )

    supplied = [(r.where, r.text) for r in pins.find_references(tmp_path) if r.supplied]
    assert supplied == [
        ("compose.yml:3", "${POSTGRES_IMAGE:?Set a digest-pinned PostgreSQL 16 image}"),
        ("compose.yml:5", "${APP_IMAGE?set APP_IMAGE}"),
    ]
    code, out, _ = run("check", tmp_path, hub)
    # Only the unpinned Dockerfile is a finding; the variables are listed.
    assert code == 1
    assert out.count("external  ") == 2
    assert out.count("unpinned  ") == 1
    assert run("update", tmp_path, hub)[0] == 0
    assert (tmp_path / "compose.yml").read_text() == compose
    assert (tmp_path / "Dockerfile").read_text() == f"FROM python:3.12-slim@{python}\n"
    assert run("check", tmp_path, hub)[0] == 0


def test_check_reports_current_stale_and_unpinned_images(tmp_path):
    hub = FakeHub()
    python = hub.publish("library/python", "3.12-slim")
    postgres = hub.publish("library/postgres", "16")
    nginx = hub.publish("library/nginx", "1.30-alpine")
    write_tree(
        tmp_path,
        {
            "Dockerfile": f"FROM python:3.12-slim@{python}\nFROM nginx:1.30-alpine\n",
            "compose.yml": f"services:\n  db:\n    image: postgres:16@{sha('1')}\n",
        },
    )

    code, out, _ = run("check", tmp_path, hub)

    assert code == 1
    assert out.splitlines() == [
        "current   docker.io/library/python:3.12-slim  Dockerfile:1",
        "unpinned  docker.io/library/nginx:1.30-alpine  Dockerfile:2  "
        f"no digest -> {nginx[:19]}",
        "stale     docker.io/library/postgres:16  compose.yml:3  "
        f"{sha('1')[:19]} -> {postgres[:19]}",
    ]
    # Tags resolve through HEAD requests alone, which Docker Hub does not
    # count as pulls.
    assert {method for method, _ in hub.served} == {"HEAD"}


def test_update_rewrites_only_the_digests_and_then_settles(tmp_path):
    hub = FakeHub()
    old_python = hub.publish("library/python", "3.12-slim", version="3.12.11")
    new_python = hub.publish("library/python", "3.12-slim", version="3.12.12")
    nginx = hub.publish("library/nginx", "1.30-alpine", version="1.30.6")
    repo, reports = tmp_path / "repo", tmp_path / "report"
    files = {
        "services/api/Dockerfile": f"# base\nFROM python:3.12-slim@{old_python}\nRUN true\n",
        "services/ui/Dockerfile": "FROM nginx:1.30-alpine\r\nCOPY site /srv\r\n",
        "docker-compose.yml": (
            f"services:\n  api:\n    image: 'python:3.12-slim@{old_python}'  # again\n"
        ),
    }
    write_tree(repo, files)

    code, out, _ = run("update", repo, hub, "--report-dir", str(reports))

    assert code == 0
    assert snapshot(repo) == {
        "docker-compose.yml": files["docker-compose.yml"]
        .replace(old_python, new_python)
        .encode(),
        "services/api/Dockerfile": files["services/api/Dockerfile"]
        .replace(old_python, new_python)
        .encode(),
        "services/ui/Dockerfile": files["services/ui/Dockerfile"]
        .replace("nginx:1.30-alpine", f"nginx:1.30-alpine@{nginx}")
        .encode(),
    }
    assert out.count("updated ") == 3
    outputs, body, message = report(reports)
    assert outputs["changed"] == "true"
    assert re.fullmatch(r"[0-9a-f]{12}", outputs["key"])
    assert outputs["title"] == (
        "Update pinned image digests: nginx:1.30-alpine, python:3.12-slim"
    )
    assert "`3.12.11` → `3.12.12`" in body
    assert (
        "Each new index matches its digest and serves linux/amd64 and linux/arm64."
        in body
    )
    assert message.startswith("Update pinned image digests\n\n- ")
    # One HEAD per tag, however often it appears; each new index is fetched
    # once to verify it and each old one once to describe it.
    assert sorted(hub.served) == sorted(
        [
            ("HEAD", "/v2/library/nginx/manifests/1.30-alpine"),
            ("HEAD", "/v2/library/python/manifests/3.12-slim"),
            ("GET", f"/v2/library/nginx/manifests/{nginx}"),
            ("GET", f"/v2/library/python/manifests/{new_python}"),
            ("GET", f"/v2/library/python/manifests/{old_python}"),
        ]
    )

    code, out, _ = run("update", repo, hub, "--report-dir", str(reports))
    outputs, body, _ = report(reports)
    assert (code, out) == (0, "Every pinned image is current.\n")
    assert outputs == {"changed": "false", "key": "", "title": ""}
    assert body == "## Pinned image digests\n\nEvery pinned image is current.\n"
    assert run("check", repo, hub)[0] == 0


def test_the_report_shows_versions_and_drops_hostile_annotations(tmp_path):
    hub = FakeHub()
    old_pg = hub.publish("library/postgres", "16", version="16.10")
    new_pg = hub.publish(
        "library/postgres", "16", version="16.11", created="2026-10-21T01:11:02Z"
    )
    old_valkey = hub.publish("valkey/valkey", "8", version="8.1.3")
    new_valkey = hub.publish(
        "valkey/valkey",
        "8",
        version="8.1.4`<img src=x onerror=alert(1)>` @everyone",
        created="<script>alert(1)</script>",
    )
    old_debian = hub.publish("library/debian", "13", version="13.1")
    new_debian = hub.publish("library/debian", "13", version="13.1")
    repo, reports = tmp_path / "repo", tmp_path / "report"
    write_tree(
        repo,
        {
            "docker-compose.yml": (
                "services:\n"
                f"  db:\n    image: postgres:16@{old_pg}\n"
                f"  cache:\n    image: valkey/valkey:8@{old_valkey}\n"
                f"  tools:\n    image: debian:13@{old_debian}\n"
            )
        },
    )

    assert run("update", repo, hub, "--report-dir", str(reports))[0] == 0

    _, body, message = report(reports)
    assert (
        "| [`postgres:16`](https://hub.docker.com/_/postgres/tags?name=16) "
        f"| `16.10` → `16.11` | 2026-10-21 | `{old_pg[:19]}` → `{new_pg[:19]}` "
        "| `docker-compose.yml` |"
    ) in body
    assert (
        "| [`valkey:8`](https://hub.docker.com/r/valkey/valkey/tags?name=8) "
        f"| unknown | unknown | `{old_valkey[:19]}` → `{new_valkey[:19]}` "
        "| `docker-compose.yml` |"
    ) in body
    assert "| `13.1` (rebuilt) |" in body
    assert "> [!WARNING]" in body and "### Review" in body
    assert message.splitlines() == [
        "Update pinned image digests",
        "",
        f"- docker.io/library/postgres:16: {old_pg[:19]} -> {new_pg[:19]} "
        "(16.10 -> 16.11)",
        f"- docker.io/valkey/valkey:8: {old_valkey[:19]} -> {new_valkey[:19]}",
        f"- docker.io/library/debian:13: {old_debian[:19]} -> {new_debian[:19]} (13.1)",
    ]
    for text in (body, message):
        for hostile in ("<img", "onerror", "@everyone", "<script"):
            assert hostile not in text


@pytest.mark.parametrize(
    "header",
    [
        "",
        "sha256:" + "a" * 63,
        "sha512:" + "a" * 128,
        "SHA256:" + "A" * 64,
        "sha256:" + "a" * 64 + " RUN curl https://example.test/x | sh",
    ],
)
def test_an_unusable_digest_header_changes_nothing(tmp_path, header):
    hub = FakeHub()
    hub.publish("library/python", "3.12-slim")
    hub.answers[("HEAD", "/v2/library/python/manifests/3.12-slim")] = Response(
        200, {"docker-content-digest": header}, b""
    )
    write_tree(tmp_path, {"Dockerfile": f"FROM python:3.12-slim@{sha('1')}\n"})
    before = snapshot(tmp_path)

    code, _, err = run("update", tmp_path, hub)

    assert code == 2
    assert "returned no usable digest" in err
    assert snapshot(tmp_path) == before


def test_an_index_that_differs_from_its_digest_changes_nothing(tmp_path):
    hub = FakeHub()
    digest = hub.publish("library/python", "3.12-slim")
    media_type, body = hub.manifests[("library/python", digest)]
    hub.manifests[("library/python", digest)] = (media_type, body + b" ")
    write_tree(tmp_path, {"Dockerfile": f"FROM python:3.12-slim@{sha('1')}\n"})
    before = snapshot(tmp_path)

    code, _, err = run("update", tmp_path, hub)

    assert code == 2
    assert f"the content served for {digest} differs" in err
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "publish,message",
    [
        ({"platforms": ("linux/amd64",)}, "does not serve linux/arm64"),
        ({"platforms": ("linux/arm64", "linux/arm")}, "does not serve linux/amd64"),
        ({"media_type": pins.DOCKER_LIST, "platforms": ()}, "does not serve linux"),
        ({"media_type": OCI_MANIFEST}, "is a single-platform manifest"),
        ({"body": b"not json"}, "is not a JSON manifest"),
        ({"body": b"[]"}, "is not a JSON manifest"),
        (
            {
                "body": json.dumps({"mediaType": {"x": 1}, "manifests": []}).encode(),
                "media_type": OCI_MANIFEST,
            },
            "is a single-platform manifest",
        ),
        (
            {
                "body": json.dumps(
                    {
                        "mediaType": pins.OCI_INDEX,
                        "manifests": [None, "junk", {"platform": {"os": 1}}],
                    }
                ).encode()
            },
            "does not serve linux/amd64, linux/arm64",
        ),
    ],
)
def test_an_index_without_every_release_platform_changes_nothing(
    tmp_path, publish, message
):
    hub = FakeHub()
    python = hub.publish("library/python", "3.12-slim")
    hub.publish("library/nginx", "1.30-alpine", **publish)
    write_tree(
        tmp_path,
        {
            "api/Dockerfile": f"FROM python:3.12-slim@{sha('1')}\n",
            "ui/Dockerfile": f"FROM nginx:1.30-alpine@{sha('2')}\n",
        },
    )
    before = snapshot(tmp_path)

    code, _, err = run("update", tmp_path, hub)

    # The python pin could move, but nothing is written while any image fails.
    assert ("GET", f"/v2/library/python/manifests/{python}") in hub.served
    assert code == 2
    assert message in err
    assert snapshot(tmp_path) == before


def test_a_file_edited_during_an_update_keeps_the_edit(tmp_path):
    hub = FakeHub()
    hub.publish("library/python", "3.12-slim")
    write_tree(tmp_path, {"Dockerfile": f"FROM python:3.12-slim@{sha('1')}\n"})
    edit = "FROM python:3.13-slim\n"

    def editing_hub(method, url, headers):
        (tmp_path / "Dockerfile").write_text(edit)  # someone edits meanwhile
        return hub(method, url, headers)

    code, _, err = run("update", tmp_path, editing_hub)

    assert code == 2
    assert "Dockerfile:1 changed during the update" in err
    assert (tmp_path / "Dockerfile").read_text() == edit


def test_one_tag_pinned_in_several_files_moves_everywhere_at_once(tmp_path):
    hub = FakeHub()
    postgres = hub.publish("library/postgres", "16")
    repo, reports = tmp_path / "repo", tmp_path / "report"
    write_tree(
        repo,
        {
            "docker-compose.yml": f"services:\n  db:\n    image: postgres:16@{sha('1')}\n",
            ".github/workflows/ci.yml": (
                "jobs:\n  t:\n    services:\n      db:\n"
                f"        image: docker.io/library/postgres:16@{sha('2')}\n"
            ),
        },
    )

    assert run("update", repo, hub, "--report-dir", str(reports))[0] == 0

    assert f"image: postgres:16@{postgres}" in (repo / "docker-compose.yml").read_text()
    assert (
        f"docker.io/library/postgres:16@{postgres}"
        in (repo / ".github/workflows/ci.yml").read_text()
    )
    outputs, body, message = report(reports)
    key = hashlib.sha256(f"docker.io/library/postgres:16@{postgres}".encode())
    assert outputs == {
        "changed": "true",
        "key": key.hexdigest()[:12],
        "title": "Update pinned image digests: postgres:16",
    }
    # Each old digest gets its own row, listing the file that carried it.
    assert body.count("| [`postgres:16`]") == 2
    assert "| `.github/workflows/ci.yml` |" in body
    assert "| `docker-compose.yml` |" in body
    assert len(message.splitlines()) == 4


def test_the_branch_key_depends_only_on_the_set_of_new_pins():
    def change(path, text, new):
        reference = pins.Reference(path, 1, 0, len(text), text, pins.parse_image(text))
        return pins.Change(reference, new)

    python = change("Dockerfile", f"python:3.12-slim@{sha('1')}", sha("2"))
    postgres = change("compose.yml", f"postgres:16@{sha('3')}", sha("4"))
    postgres_again = change("ci.yml", "docker.io/library/postgres:16", sha("4"))
    moved = change("compose.yml", f"postgres:16@{sha('3')}", sha("5"))

    key = pins.change_key([python, postgres])
    assert key == pins.change_key([postgres, python])
    assert key == pins.change_key([python, postgres, postgres_again])
    assert key != pins.change_key([python, moved])
    assert pins.change_key([]) == ""


def test_registry_requests_answer_the_bearer_challenge_and_reuse_the_token():
    hub = FakeHub()
    digest = hub.publish("library/node", "22-alpine")
    registry, image = pins.Registry(hub), pins.parse_image("node:22-alpine")

    assert registry.current_digest(image) == digest
    assert registry.current_digest(image) == digest

    token_requests = [c for c in hub.calls if c[1] == "auth.docker.io"]
    assert len(token_requests) == 1
    _, _, path, query, headers = token_requests[0]
    assert path == "/token"
    assert dict(urllib.parse.parse_qsl(query)) == {
        "service": "registry.docker.io",
        "scope": "repository:library/node:pull",
    }
    assert "Authorization" not in headers
    registry_calls = [c for c in hub.calls if c[1] == "registry-1.docker.io"]
    assert [c[4].get("Authorization") for c in registry_calls] == [
        None,
        "Bearer token-library-node",
        "Bearer token-library-node",
    ]
    assert all(c[4]["Accept"].startswith(pins.OCI_INDEX) for c in registry_calls)


@pytest.mark.parametrize(
    "challenge",
    [
        'Bearer realm="http://auth.docker.io/token",service="registry.docker.io"',
        'Basic realm="registry"',
        "",
    ],
)
def test_other_sign_in_schemes_are_refused_before_any_token_request(challenge):
    hub = FakeHub()
    hub.publish("library/node", "22-alpine")
    hub.answers[("HEAD", "/v2/library/node/manifests/22-alpine")] = Response(
        401, {"www-authenticate": challenge}, b""
    )

    with pytest.raises(pins.PinError, match="unsupported sign-in"):
        pins.Registry(hub).current_digest(pins.parse_image("node:22-alpine"))
    assert {c[1] for c in hub.calls} == {"registry-1.docker.io"}


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"token": ""}',
        b'{"token": "two words"}',
        b'{"token": "line\\nbreak"}',
        json.dumps({"token": "x" * 9000}).encode(),
    ],
)
def test_unusable_tokens_are_refused(body):
    hub = FakeHub()
    hub.publish("library/node", "22-alpine")
    hub.answers[("GET", "/token")] = Response(200, {}, body)

    with pytest.raises(pins.PinError, match="no usable token"):
        pins.Registry(hub).current_digest(pins.parse_image("node:22-alpine"))


@pytest.mark.parametrize(
    "status,message",
    [
        (429, "is limiting requests"),
        (404, "answered HTTP 404"),
        (500, "answered HTTP 500"),
        (307, "answered HTTP 307"),
    ],
)
def test_registry_failures_stop_the_update_and_change_nothing(
    tmp_path, status, message
):
    hub = FakeHub()
    hub.publish("library/python", "3.12-slim")
    hub.answers[("HEAD", "/v2/library/python/manifests/3.12-slim")] = Response(
        status, {}, b""
    )
    write_tree(tmp_path, {"Dockerfile": f"FROM python:3.12-slim@{sha('1')}\n"})
    before = snapshot(tmp_path)

    code, _, err = run("update", tmp_path, hub)

    assert code == 2
    assert message in err
    assert snapshot(tmp_path) == before


def test_versions_come_from_the_amd64_entry_or_else_the_index():
    amd64 = {"os": "linux", "architecture": "amd64"}
    arm64 = {"os": "linux", "architecture": "arm64"}
    assert pins.describe(
        {
            "manifests": [
                {"platform": arm64, "annotations": {VERSION: "9"}},
                {
                    "platform": amd64,
                    "annotations": {VERSION: "8.1", CREATED: "2026-10-01T02:03:04Z"},
                },
            ]
        }
    ) == pins.About("8.1", "2026-10-01")
    assert pins.describe(
        {"annotations": {VERSION: "8.2", CREATED: "1 October"}, "manifests": []}
    ) == pins.About("8.2", None)
    assert pins.describe({"manifests": "junk", "annotations": ["junk"]}) == pins.About()
    assert pins.platforms(
        {
            "manifests": [
                None,
                {"platform": "x"},
                {"platform": {"os": 1}},
                {"platform": amd64},
            ]
        }
    ) == {"linux/amd64"}


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("image-pins-tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = root / "cert.pem", root / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


@contextmanager
def https_server(tls_files, routes):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, head):
            requests.append((self.command, self.path))
            status, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(status)
            for header, value in headers.items():
                self.send_header(header, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self._answer(head=False)

        def do_HEAD(self):
            self._answer(head=True)

        def log_message(self, *args):
            pass

    class Server(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # clients that stop reading early are expected here

    server = Server(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(*tls_files)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], requests
    finally:
        server.shutdown()
        server.server_close()


def test_the_transport_verifies_tls_and_refuses_redirects_and_large_bodies(
    tls_files, monkeypatch
):
    for name in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    routes = {
        "/v2/app/manifests/1": (200, {"Docker-Content-Digest": sha("a")}, b""),
        "/moved": (302, {"Location": "/v2/app/manifests/1"}, b""),
        "/large": (200, {}, b"x" * (pins.MAX_RESPONSE_BYTES + 1)),
    }
    with https_server(tls_files, routes) as (port, requests):
        origin = f"https://localhost:{port}"
        # An untrusted certificate stops the request before any HTTP exchange.
        with pytest.raises(pins.PinError, match="failed"):
            pins.https_fetch("HEAD", origin + "/v2/app/manifests/1", {})
        assert requests == []

        # Test-only CA; certificate and hostname verification stay on.
        original = ssl.create_default_context
        monkeypatch.setattr(
            pins.ssl,
            "create_default_context",
            lambda: original(cafile=str(tls_files[0])),
        )
        answer = pins.https_fetch("HEAD", origin + "/v2/app/manifests/1", {})
        assert (answer.status, answer.headers["docker-content-digest"]) == (
            200,
            sha("a"),
        )
        with pytest.raises(pins.PinError, match="failed"):
            pins.https_fetch("HEAD", f"https://127.0.0.1:{port}/v2/app/manifests/1", {})

        # A redirect comes back as its own status, and its target stays unrequested.
        assert pins.https_fetch("GET", origin + "/moved", {}).status == 302
        assert requests == [("HEAD", "/v2/app/manifests/1"), ("GET", "/moved")]

        with pytest.raises(pins.PinError, match="exceeds 1 MiB"):
            pins.https_fetch("GET", origin + "/large", {})

    with pytest.raises(pins.PinError, match="without HTTPS"):
        pins.https_fetch("GET", "http://registry.example.test/v2/", {})


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_every_third_party_image_in_the_repository_is_pinned():
    references = pins.find_references(ROOT)

    assert [f"{r.where} {r.text}: {r.problem}" for r in references if r.problem] == []
    third_party = [r for r in references if r.image and not r.keen]
    assert [r.where for r in third_party if not r.image.digest] == []
    assert {r.image.repository for r in third_party} >= {
        "library/python",
        "library/node",
        "library/nginx",
        "library/postgres",
        "library/debian",
        "valkey/valkey",
        "moby/buildkit",
        "tonistiigi/binfmt",
    }
    # Only KEEN's own release and local build images follow tags alone.
    assert {r.image.name for r in references if r.keen} == set(pins.KEEN_IMAGES)
    assert {path for path, _ in pins.tracked_files(ROOT)} >= {
        "services/api/Dockerfile",
        "services/ui/Dockerfile",
        "docker-compose.yml",
        "docker-compose.build.yml",
        ".github/workflows/tests.yml",
        ".github/workflows/release.yml",
    }


def test_pinned_indexes_serve_every_platform_a_release_builds():
    steps = _workflow("release.yml")["jobs"]["publish"]["steps"]
    builds = [
        step["with"]["platforms"]
        for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 2
    assert all(set(p.split(",")) == set(pins.RELEASE_PLATFORMS) for p in builds)


def test_workflow_actions_are_pinned_and_scripts_read_values_from_env():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_name, job in yaml.safe_load(path.read_text())["jobs"].items():
            if "uses" in job:
                assert job["uses"].startswith("./.github/workflows/"), job_name
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(
                        r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]
                    ), (path.name, step["uses"])
                # Expressions expand before the shell parses a script, so
                # values reach scripts through environment variables.
                assert "${{" not in step.get("run", ""), (path.name, step.get("name"))


def test_the_pin_workflow_reads_registries_before_any_token_and_never_merges():
    workflow = _workflow("docker-image-pins.yml")
    assert set(workflow[True]) == {"schedule", "workflow_dispatch"}  # YAML's "on"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["BASE_BRANCH"] == "main"
    job = workflow["jobs"]["update"]
    assert job["if"] == "github.repository_owner == 'FERCBusters'"
    # GITHUB_TOKEN stays read-only; the App token makes every change.
    assert "permissions" not in job
    steps = [step["name"] for step in job["steps"]]
    assert steps == [
        "Checkout",
        "Resolve tags and update digests",
        "Check the GitHub App settings",
        "Create GitHub App token",
        "Propose the new digests",
    ]
    by_name = {step["name"]: step for step in job["steps"]}
    assert by_name["Checkout"]["with"] == {
        "ref": "${{ env.BASE_BRANCH }}",
        "persist-credentials": False,
    }
    # Registry responses are parsed before the App token exists, with no
    # secret or token in the step's environment.
    resolve = by_name["Resolve tags and update digests"]
    assert "env" not in resolve
    assert "python3 -I tools/docker_image_pins.py update" in resolve["run"]
    app = by_name["Create GitHub App token"]
    assert app["if"] == "steps.pins.outputs.changed == 'true'"
    assert {k: v for k, v in app["with"].items() if k.startswith("permission-")} == {
        "permission-contents": "write",
        "permission-pull-requests": "write",
        "permission-workflows": "write",
    }
    propose = by_name["Propose the new digests"]
    assert propose["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    # A maintainer merges; nothing here approves, merges or enables auto-merge.
    scripts = "\n".join(step.get("run", "") for step in job["steps"]).lower()
    for action in ("pr merge", "pr review", "--auto", "/merge", "automerge"):
        assert action not in scripts
