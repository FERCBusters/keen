#!/usr/bin/env python3
"""Keep Keen's third-party container images pinned to reviewed digests.

Usage:
  docker_image_pins.py check [--root DIR]
  docker_image_pins.py update [--root DIR] [--report-dir DIR]

Every third-party image that Keen builds from or runs is written as
``name:tag@sha256:<digest>``. The tag names the upstream line the image
follows. The digest identifies one multi-platform image index, so Docker pulls
that exact content even after the tag moves to another image.

check   lists each reference and its state. It exits 1 when a tag now resolves
        to another index, or when a reference is unpinned or cannot be pinned.
update  rewrites stale and missing digests in place once each new index
        matches its digest and serves every release platform. --report-dir
        receives pr-body.md, commit-message.txt and outputs.txt (key=value
        lines for GitHub Actions).

A Compose value written as ${VAR:?message} names an image that the deployment
supplies. check lists it as external, and neither command pins it.

Both commands exit 2 when a registry request fails, and update also exits 2
for a reference it cannot pin; every file then stays unchanged. Tags are
resolved with HEAD requests, which Docker Hub leaves out of its pull limits,
and update fetches each old and new index once to verify and describe it.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]

# Release images are built for these platforms (.github/workflows/release.yml),
# so every pinned index must serve both of them.
RELEASE_PLATFORMS = ("linux/amd64", "linux/arm64")

# Keen's own images follow release version tags and are pinned by the release
# process, so these names are left alone.
KEEN_IMAGES = frozenset(
    {
        "ghcr.io/fercbusters/keen-api",
        "ghcr.io/fercbusters/keen-ui",
        "keen-api",
        "keen-ui",
    }
)

WORKFLOW_DIRS = (".github/workflows", ".forgejo/workflows", ".gitea/workflows")
SKIP_DIRS = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "test-results",
        "venv",
    }
)

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
INDEX_TYPES = frozenset({OCI_INDEX, DOCKER_LIST})
ACCEPT = ", ".join(
    (
        OCI_INDEX,
        DOCKER_LIST,
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
REGISTRY_HOSTS = {"docker.io": "registry-1.docker.io"}
USER_AGENT = "keen-docker-image-pins"
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1024 * 1024

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_TAG = re.compile(r"\w[\w.-]{0,127}")
_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_DOMAIN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?")
_TOKEN = re.compile(r"[\x21-\x7e]{1,8192}")
_CHALLENGE_FIELD = re.compile(r'(\w+)="([^"]*)"')
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}")
_CREATED = re.compile(
    r"(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)

_FROM = re.compile(
    r"\s*FROM\s+(?:--\S+\s+)*(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*",
    re.IGNORECASE,
)
_COPY_FROM = re.compile(r"\s*COPY\s.*?(?<=\s)--from=(?P<ref>\S+)", re.IGNORECASE)
_RUN = re.compile(r"\s*RUN\s", re.IGNORECASE)
_MOUNT_FROM = re.compile(r"--mount=\S*?\bfrom=(?P<ref>[^,\s]+)")
_IMAGE_KEY = re.compile(r"\s*(?:-\s+)?image:[ \t]*(?P<value>[^#]*?)[ \t]*(?:#.*)?")
_CONTAINER_KEY = re.compile(r"\s*container:[ \t]*(?P<value>[^#]*?)[ \t]*(?:#.*)?")
_DOCKER_USES = re.compile(
    r"\s*(?:-\s+)?uses:\s*(?P<q>[\"']?)docker://(?P<ref>[^\s\"'#]+)(?P=q)"
)
_IMAGE_OPTION = re.compile(r"(?:^|[\s,])image=(?P<ref>[^\s,\"'#]+)")
_DEFAULTED = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-(?P<ref>[^${}]*)\}")
_REQUIRED = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?\?[^${}]*\}")
_COMPOSE_FILE = re.compile(r"(?:docker-)?compose(?:[.-][\w.-]+)?\.ya?ml")


class PinError(Exception):
    """A reference or registry response that cannot be pinned safely."""


@dataclass(frozen=True)
class Image:
    name: str  # as written, without the tag or digest
    registry: str
    repository: str
    tag: str | None
    digest: str | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.registry, self.repository, self.tag or "")

    @property
    def label(self) -> str:
        return f"{self.registry}/{self.repository}:{self.tag}"

    @property
    def short(self) -> str:
        return f"{self.repository.rsplit('/', 1)[-1]}:{self.tag}"

    def pinned(self, digest: str) -> str:
        return f"{self.name}:{self.tag}@{digest}"


@dataclass(frozen=True)
class Reference:
    path: str  # relative to the root, with forward slashes
    line: int  # 1-based
    start: int  # offsets of the reference text within its line
    end: int
    text: str
    image: Image | None = None
    keen: bool = False
    supplied: bool = False  # a required variable set by the deployment
    problem: str | None = None

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Change:
    reference: Reference
    new: str


@dataclass(frozen=True)
class About:
    version: str | None = None
    created: str | None = None


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes


Fetch = Callable[[str, str, Mapping[str, str]], Response]


def parse_image(text: str) -> Image:
    """Split an image reference into its registry, repository, tag and digest."""
    rest, at, digest = text.partition("@")
    if at and not DIGEST.fullmatch(digest):
        raise PinError("has a malformed digest; use sha256: and 64 lower-case hex")
    slash, colon = rest.rfind("/"), rest.rfind(":")
    name, tag = (rest[:colon], rest[colon + 1 :]) if colon > slash else (rest, "")
    parts = name.split("/")
    first = parts[0]
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, parts[1:]
        if not _DOMAIN.fullmatch(registry):
            raise PinError("names a malformed registry")
    else:
        registry, path = "docker.io", parts
    if not all(_COMPONENT.fullmatch(part) for part in path):
        raise PinError("is not a valid image name")
    if tag and not _TAG.fullmatch(tag):
        raise PinError("has a malformed tag")
    if registry in ("index.docker.io", "registry-1.docker.io"):
        registry = "docker.io"
    if registry == "docker.io" and len(path) == 1:
        path = ["library", *path]
    return Image(name, registry, "/".join(path), tag or None, digest or None)


def _reference(path: str, number: int, start: int, text: str) -> Reference:
    end = start + len(text)
    if "$" in text:
        return Reference(
            path,
            number,
            start,
            end,
            text,
            problem="comes from a variable; write the image out in full to pin it",
        )
    try:
        image = parse_image(text)
    except PinError as error:
        return Reference(path, number, start, end, text, problem=str(error))
    if image.name in KEEN_IMAGES:
        return Reference(path, number, start, end, text, image, keen=True)
    if image.tag is None:
        return Reference(
            path,
            number,
            start,
            end,
            text,
            image,
            problem="has no tag; name the tag whose updates it follows",
        )
    return Reference(path, number, start, end, text, image)


def _lines(text: str) -> Iterable[tuple[int, str]]:
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        yield number, line.rstrip("\r\n")


def _scan_dockerfile(path: str, text: str) -> list[Reference]:
    found, stages = [], set()

    def external(ref: str) -> bool:
        return ref.lower() not in stages and not ref.isdigit()

    for number, line in _lines(text):
        if line.lstrip().startswith("#"):
            continue
        if match := _FROM.fullmatch(line):
            ref = match["ref"]
            if ref.lower() != "scratch" and external(ref):
                found.append(_reference(path, number, match.start("ref"), ref))
            if match["stage"]:
                stages.add(match["stage"].lower())
        elif match := _COPY_FROM.match(line):
            if external(match["ref"]):
                found.append(_reference(path, number, match.start("ref"), match["ref"]))
        elif _RUN.match(line):
            for match in _MOUNT_FROM.finditer(line):
                if external(match["ref"]):
                    found.append(
                        _reference(path, number, match.start("ref"), match["ref"])
                    )
    return found


def _value_reference(path: str, number: int, match: re.Match) -> Reference | None:
    """The image named by a YAML value, or None for a key that opens a mapping."""
    value, start = match["value"], match.start("value")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value, start = value[1:-1], start + 1
    if _REQUIRED.fullmatch(value):
        return Reference(path, number, start, start + len(value), value, supplied=True)
    if defaulted := _DEFAULTED.fullmatch(value):
        value, start = defaulted["ref"], start + defaulted.start("ref")
    return _reference(path, number, start, value) if value else None


def _scan_compose(path: str, text: str) -> list[Reference]:
    found = []
    for number, line in _lines(text):
        if (match := _IMAGE_KEY.fullmatch(line)) and (
            ref := _value_reference(path, number, match)
        ):
            found.append(ref)
    return found


def _scan_workflow(path: str, text: str) -> list[Reference]:
    found = []
    for number, line in _lines(text):
        if line.lstrip().startswith("#"):
            continue
        for pattern in (_IMAGE_KEY, _CONTAINER_KEY):
            if (match := pattern.fullmatch(line)) and (
                ref := _value_reference(path, number, match)
            ):
                found.append(ref)
        if match := _DOCKER_USES.match(line):
            found.append(_reference(path, number, match.start("ref"), match["ref"]))
        for match in _IMAGE_OPTION.finditer(line):
            found.append(_reference(path, number, match.start("ref"), match["ref"]))
    return found


_SCANNERS = {
    "dockerfile": _scan_dockerfile,
    "compose": _scan_compose,
    "workflow": _scan_workflow,
}


def _kind(directory: str, name: str) -> str | None:
    if directory in WORKFLOW_DIRS and name.endswith((".yml", ".yaml")):
        return "workflow"
    if (
        name == "Dockerfile"
        or name.startswith("Dockerfile.")
        or name.endswith(".Dockerfile")
    ):
        return "dockerfile"
    if _COMPOSE_FILE.fullmatch(name):
        return "compose"
    return None


def tracked_files(root: Path) -> list[tuple[str, str]]:
    """Dockerfiles, Compose files and CI workflows under the root, with their kind."""
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIP_DIRS)
        relative = Path(directory).relative_to(root)
        for name in sorted(files):
            if kind := _kind(relative.as_posix(), name):
                found.append(((relative / name).as_posix(), kind))
    return found


def find_references(root: Path) -> list[Reference]:
    """Every image reference in the tracked files, in file and line order."""
    references = []
    for path, kind in tracked_files(root):
        text = (root / path).read_bytes().decode("utf-8")
        references.extend(_SCANNERS[kind](path, text))
    return references


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib then reports the redirect as an HTTP error


def _read(status: int, headers, stream) -> Response:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise PinError("a registry response exceeds 1 MiB")
    return Response(status, {k.lower(): v for k, v in headers.items()}, body)


def https_fetch(method: str, url: str, headers: Mapping[str, str]) -> Response:
    """Send one verified HTTPS request without following redirects."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise PinError(f"refusing a request to {parts.netloc} without HTTPS")
    opener = urllib.request.build_opener(
        _RefuseRedirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(url, headers=dict(headers), method=method)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return _read(response.status, response.headers, response)
    except urllib.error.HTTPError as error:
        return _read(error.code, error.headers, error)
    except (OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", error)
        raise PinError(f"{method} {parts.netloc} failed: {reason}") from None


class Registry:
    """Reads image manifests from OCI distribution registries."""

    def __init__(self, fetch: Fetch = https_fetch) -> None:
        self._fetch = fetch
        self._tokens: dict[tuple[str, str], str] = {}

    def current_digest(self, image: Image) -> str:
        """The digest the registry serves for the image's tag (one HEAD request)."""
        response = self._manifest("HEAD", image, image.tag or "")
        digest = response.headers.get("docker-content-digest", "").strip()
        if not DIGEST.fullmatch(digest):
            raise PinError(f"{image.label}: the registry returned no usable digest")
        return digest

    def index(self, image: Image, digest: str) -> dict:
        """The image index stored under a digest, checked against that digest."""
        response = self._manifest("GET", image, digest)
        if "sha256:" + hashlib.sha256(response.body).hexdigest() != digest:
            raise PinError(f"{image.label}: the content served for {digest} differs")
        try:
            document = json.loads(response.body)
        except ValueError:
            document = None
        if not isinstance(document, dict):
            raise PinError(f"{image.label}: {short(digest)} is not a JSON manifest")
        media_type = document.get("mediaType")
        if not isinstance(media_type, str):
            media_type = response.headers.get("content-type", "").split(";")[0]
        if media_type.strip() not in INDEX_TYPES:
            raise PinError(
                f"{image.label}: {short(digest)} is a single-platform manifest; "
                "pin a tag that publishes a multi-platform index"
            )
        return document

    def release_index(self, image: Image, digest: str) -> dict:
        """The index under a digest, which must serve every release platform."""
        document = self.index(image, digest)
        missing = [p for p in RELEASE_PLATFORMS if p not in platforms(document)]
        if missing:
            raise PinError(
                f"{image.label}: {short(digest)} does not serve {', '.join(missing)}"
            )
        return document

    def _manifest(self, method: str, image: Image, reference: str) -> Response:
        host = REGISTRY_HOSTS.get(image.registry, image.registry)
        url = f"https://{host}/v2/{image.repository}/manifests/{reference}"
        scope = (host, image.repository)
        headers = {"Accept": ACCEPT, "User-Agent": USER_AGENT}
        if scope in self._tokens:
            headers["Authorization"] = f"Bearer {self._tokens[scope]}"
        response = self._fetch(method, url, headers)
        if response.status == 401:
            challenge = response.headers.get("www-authenticate", "")
            self._tokens[scope] = self._token(image, challenge)
            headers["Authorization"] = f"Bearer {self._tokens[scope]}"
            response = self._fetch(method, url, headers)
        if response.status == 429:
            raise PinError(f"{host} is limiting requests; try again later")
        if response.status != 200:
            raise PinError(
                f"{image.label}: {host} answered HTTP {response.status} "
                f"for {short(reference)}"
            )
        return response

    def _token(self, image: Image, challenge: str) -> str:
        scheme, _, parameters = challenge.partition(" ")
        fields = dict(_CHALLENGE_FIELD.findall(parameters))
        realm = fields.get("realm", "")
        if scheme.lower() != "bearer" or urllib.parse.urlsplit(realm).scheme != "https":
            raise PinError(f"{image.label}: the registry asks for unsupported sign-in")
        query = {
            "service": fields.get("service", ""),
            "scope": fields.get("scope") or f"repository:{image.repository}:pull",
        }
        separator = "&" if "?" in realm else "?"
        url = (
            realm
            + separator
            + urllib.parse.urlencode(
                {name: value for name, value in query.items() if value}
            )
        )
        response = self._fetch(
            "GET", url, {"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        if response.status != 200:
            raise PinError(
                f"{image.label}: the token service answered HTTP {response.status}"
            )
        try:
            data = json.loads(response.body)
        except ValueError:
            data = None
        token = (
            data.get("token") or data.get("access_token")
            if isinstance(data, dict)
            else None
        )
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise PinError(f"{image.label}: the token service returned no usable token")
        return token


def platforms(document: dict) -> set[str]:
    """The os/architecture pairs an image index serves."""
    found = set()
    entries = document.get("manifests")
    for entry in entries if isinstance(entries, list) else ():
        platform = entry.get("platform") if isinstance(entry, dict) else None
        if not isinstance(platform, dict):
            continue
        os_name, architecture = platform.get("os"), platform.get("architecture")
        if isinstance(os_name, str) and isinstance(architecture, str):
            found.add(f"{os_name}/{architecture}")
    return found


def describe(document: dict) -> About:
    """The upstream version and build date an index declares, when well formed.

    Registry content is untrusted, so values outside a narrow pattern are
    dropped before they reach a pull request or commit message.
    """
    annotations = document.get("annotations")
    entries = document.get("manifests")
    for entry in entries if isinstance(entries, list) else ():
        platform = entry.get("platform") if isinstance(entry, dict) else None
        if (
            isinstance(platform, dict)
            and platform.get("os") == "linux"
            and platform.get("architecture") == "amd64"
            and isinstance(entry.get("annotations"), dict)
        ):
            annotations = entry["annotations"]
            break
    if not isinstance(annotations, dict):
        return About()
    version = annotations.get("org.opencontainers.image.version")
    created = annotations.get("org.opencontainers.image.created")
    built = _CREATED.fullmatch(created) if isinstance(created, str) else None
    if not (isinstance(version, str) and _VERSION.fullmatch(version)):
        version = None
    return About(version, built[1] if built else None)


def short(digest: str) -> str:
    return digest[:19] if DIGEST.fullmatch(digest) else digest


def _hub_url(image: Image) -> str | None:
    if image.registry != "docker.io":
        return None
    namespace, _, name = image.repository.partition("/")
    path = f"_/{name}" if namespace == "library" else f"r/{image.repository}"
    return f"https://hub.docker.com/{path}/tags?name={urllib.parse.quote(image.tag)}"


def _resolve(references: Sequence[Reference], registry: Registry) -> dict:
    images = {r.image.key: r.image for r in references}
    return {key: registry.current_digest(images[key]) for key in sorted(images)}


def _pinnable(references: Sequence[Reference]) -> list[Reference]:
    return [r for r in references if r.image and not r.keen and not r.problem]


def change_key(changes: Sequence[Change]) -> str:
    """A short name for a set of new pins, independent of file order."""
    pins = sorted({f"{c.reference.image.label}@{c.new}" for c in changes})
    return hashlib.sha256("\n".join(pins).encode()).hexdigest()[:12] if pins else ""


def title(changes: Sequence[Change]) -> str:
    names = sorted({c.reference.image.short for c in changes})
    if not names:
        return ""
    shown = ", ".join(names[:3])
    if len(names) > 3:
        shown += f" and {len(names) - 3} more"
    return f"Update pinned image digests: {shown}"


def _rows(changes: Sequence[Change]) -> list[tuple[Change, list[str]]]:
    rows: dict[tuple, tuple[Change, list[str]]] = {}
    for change in changes:
        image = change.reference.image
        key = (image.label, image.digest, change.new)
        _, paths = rows.setdefault(key, (change, []))
        if change.reference.path not in paths:
            paths.append(change.reference.path)
    return list(rows.values())


def _version_change(old: About | None, new: About) -> str:
    if new.version is None:
        return "unknown"
    if old is None or old.version is None:
        return f"`{new.version}`"
    if old.version == new.version:
        return f"`{new.version}` (rebuilt)"
    return f"`{old.version}` → `{new.version}`"


def pull_request_body(changes: Sequence[Change], about: Mapping) -> str:
    """Markdown describing the changes, built only from validated values."""
    lines = ["## Pinned image digests", ""]
    if not changes:
        return "\n".join([*lines, "Every pinned image is current.", ""])
    lines += [
        "> [!WARNING]",
        "> This pull request is generated automatically. Each tag below now",
        "> resolves to a different image index upstream. Review the version",
        "> changes and upstream release notes, and let CI pass, before merging.",
        "",
        "| Image | Version | Built | Digest | Files |",
        "| --- | --- | --- | --- | --- |",
    ]
    for change, paths in _rows(changes):
        image = change.reference.image
        url = _hub_url(image)
        name = f"[`{image.short}`]({url})" if url else f"`{image.label}`"
        old = about.get((image.key, image.digest)) if image.digest else None
        new = about[(image.key, change.new)]
        digests = (
            f"`{short(image.digest)}` → `{short(change.new)}`"
            if image.digest
            else f"unpinned → `{short(change.new)}`"
        )
        files = ", ".join(f"`{path}`" for path in paths)
        lines.append(
            f"| {name} | {_version_change(old, new)} | "
            f"{new.created or 'unknown'} | {digests} | {files} |"
        )
    lines += [
        "",
        "Each new index matches its digest and serves "
        f"{' and '.join(RELEASE_PLATFORMS)}.",
        "",
        "### Review",
        "",
        "- [ ] Version changes and upstream release notes reviewed",
        "- [ ] CI passed on this branch",
        "",
    ]
    return "\n".join(lines)


def commit_message(changes: Sequence[Change], about: Mapping) -> str:
    lines = ["Update pinned image digests", ""]
    for change, paths in _rows(changes):
        image = change.reference.image
        old = about.get((image.key, image.digest)) if image.digest else None
        new = about[(image.key, change.new)]
        line = f"- {image.label}: {short(image.digest or 'unpinned')} -> "
        line += short(change.new)
        if new.version and old and old.version and old.version != new.version:
            line += f" ({old.version} -> {new.version})"
        elif new.version:
            line += f" ({new.version})"
        lines.append(line)
    return "\n".join(lines) + "\n"


def outputs(changes: Sequence[Change]) -> str:
    return (
        f"changed={'true' if changes else 'false'}\n"
        f"key={change_key(changes)}\n"
        f"title={title(changes)}\n"
    )


def _rewritten(root: Path, changes: Sequence[Change]) -> dict[str, bytes]:
    """New contents for every changed file, computed before anything is written."""
    by_path: dict[str, list[Change]] = {}
    for change in changes:
        by_path.setdefault(change.reference.path, []).append(change)
    contents = {}
    for path, in_file in sorted(by_path.items()):
        lines = (root / path).read_bytes().decode("utf-8").splitlines(keepends=True)
        for change in sorted(
            in_file, key=lambda c: (c.reference.line, -c.reference.start)
        ):
            ref = change.reference
            line = lines[ref.line - 1]
            if line[ref.start : ref.end] != ref.text:
                raise PinError(f"{ref.where} changed during the update")
            lines[ref.line - 1] = (
                line[: ref.start] + ref.image.pinned(change.new) + line[ref.end :]
            )
        contents[path] = "".join(lines).encode("utf-8")
    return contents


def check(root: Path, registry: Registry, out: TextIO) -> int:
    references = find_references(root)
    problems = [r for r in references if r.problem]
    for ref in problems:
        print(f"problem   {ref.where}: {ref.text} {ref.problem}", file=out)
    for ref in references:
        if ref.supplied:
            print(f"external  {ref.where}  {ref.text} (set when deploying)", file=out)
    pinnable = _pinnable(references)
    current = _resolve(pinnable, registry)
    outdated = bool(problems)
    for ref in pinnable:
        digest, image = current[ref.image.key], ref.image
        if image.digest == digest:
            print(f"current   {image.label}  {ref.where}", file=out)
            continue
        outdated = True
        state = "stale   " if image.digest else "unpinned"
        before = short(image.digest) if image.digest else "no digest"
        print(
            f"{state}  {image.label}  {ref.where}  {before} -> {short(digest)}",
            file=out,
        )
    return 1 if outdated else 0


def update(
    root: Path, registry: Registry, out: TextIO, report_dir: Path | None = None
) -> int:
    references = find_references(root)
    problems = [r for r in references if r.problem]
    if problems:
        for ref in problems:
            print(f"problem   {ref.where}: {ref.text} {ref.problem}", file=out)
        raise PinError("fix the references above before updating")
    pinnable = _pinnable(references)
    current = _resolve(pinnable, registry)
    stale = [r for r in pinnable if r.image.digest != current[r.image.key]]

    about: dict[tuple, About] = {}
    for ref in stale:
        image, new = ref.image, current[ref.image.key]
        if (image.key, new) not in about:
            about[(image.key, new)] = describe(registry.release_index(image, new))
        if image.digest and (image.key, image.digest) not in about:
            try:  # Only describes the change; the old image may have gone.
                about[(image.key, image.digest)] = describe(
                    registry.index(image, image.digest)
                )
            except PinError:
                about[(image.key, image.digest)] = About()

    changes = [Change(ref, current[ref.image.key]) for ref in stale]
    for path, content in _rewritten(root, changes).items():
        (root / path).write_bytes(content)

    for change in changes:
        ref = change.reference
        before = short(ref.image.digest) if ref.image.digest else "no digest"
        print(
            f"updated   {ref.image.label}  {ref.where}  {before} -> {short(change.new)}",
            file=out,
        )
    if not changes:
        print("Every pinned image is current.", file=out)
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "pr-body.md").write_text(pull_request_body(changes, about))
        (report_dir / "commit-message.txt").write_text(commit_message(changes, about))
        (report_dir / "outputs.txt").write_text(outputs(changes))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: Registry | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="report stale or missing pins")
    update_parser = commands.add_parser("update", help="rewrite stale or missing pins")
    for command in (check_parser, update_parser):
        command.add_argument("--root", type=Path, default=ROOT)
    update_parser.add_argument("--report-dir", type=Path, metavar="DIR")
    args = parser.parse_args(argv)
    registry = registry or Registry()
    try:
        if args.command == "check":
            return check(args.root, registry, out)
        return update(args.root, registry, out, args.report_dir)
    except PinError as error:
        print(f"error: {error}", file=err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
