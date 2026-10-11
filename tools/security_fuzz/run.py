#!/usr/bin/env python3
"""Bounded KEEN security probes; run --help for the local development workflow."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import random
import ssl
import stat
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

PAYLOADS = (
    "<script>alert(1)</script>",
    "<svg/onload=alert(1)>",
    '<a href="javascript:alert(1)">click</a>',
    "' OR 1=1--",
    "../../../../etc/passwd",
    "\r\nX-Injected: yes",
    "https://user:password@example.org/",
    "javascript:alert(1)",
    '=HYPERLINK("https://example.org")',
    "NaN",
    "1e309",
    "\x00",
)


def offline(seed, count):
    """Exercise pure helpers with synthetic data, without importing the API app."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/api"))
    from html.parser import HTMLParser

    from app.security.csv import spreadsheet_cell
    from app.security.rich_text import RICH_TEXT_ALLOWED_TAGS, sanitize_rich_text_html
    from app.security.urls import external_url

    class Inspector(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag in RICH_TEXT_ALLOWED_TAGS, "unexpected HTML tag"
            for key, value in attrs:
                assert tag == "a" and key in {"href", "target", "rel"}, (
                    "unexpected attribute"
                )
                if key == "href":
                    scheme = urlsplit(value).scheme.lower()
                    assert scheme in {"", "http", "https", "mailto", "tel"}, (
                        "active URL"
                    )
                    assert not value.startswith("//"), "protocol-relative URL"

    rng = random.Random(seed)
    results = []
    for index in range(count):
        value = rng.choice(PAYLOADS)
        value = rng.choice(("", " ", "\t", "\ufeff")) + value * rng.randint(1, 8)
        try:
            Inspector(convert_charrefs=True).feed(sanitize_rich_text_html(value))
            try:
                accepted = external_url(value)
            except ValueError:
                accepted = None
            if accepted:
                parsed = urlsplit(accepted)
                assert parsed.scheme in {"https", "http"} and parsed.hostname
                assert not parsed.username and not parsed.password
                assert "\\" not in accepted
                assert all(ord(c) >= 32 and ord(c) != 127 for c in accepted)
            cell = spreadsheet_cell(value)
            if value.lstrip(" \t\r\n\v\f\x00\ufeff").startswith(("=", "+", "-", "@")):
                assert cell.startswith("'"), "CSV formula remains active"
            results.append({"case": index, "result": "pass"})
        except Exception as exc:
            # Exception messages can contain input; only synthetic case IDs and types are saved.
            results.append(
                {"case": index, "result": "finding", "type": type(exc).__name__}
            )
    return results


def target(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an HTTP(S) loopback origin without credentials, query or fragment"
        )
    if not ipaddress.ip_address(parsed.hostname).is_loopback:
        raise ValueError("Only literal loopback addresses are supported")
    if parsed.path not in {"", "/"}:
        raise ValueError("Put the API prefix in each manifest path")
    return parsed


def load_manifest(path):
    path = Path(path)
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Manifest may contain cookies; use chmod 600")
    if path.stat().st_size > 262144:
        raise ValueError("Manifest exceeds 256 KiB")
    data = json.loads(path.read_text())
    probes = data["probes"]
    if not isinstance(probes, list) or not 1 <= len(probes) <= 200:
        raise ValueError("Supply 1–200 probes")
    for index, probe in enumerate(probes):
        path = probe["path"]
        if (
            not isinstance(path, str)
            or len(path) > 8192
            or not path.startswith("/")
            or path.startswith("//")
            or any(ord(c) < 32 or ord(c) > 126 for c in path)
            or "\\" in path
            or "#" in path
        ):
            raise ValueError("Invalid probe path")
        if probe.get("method", "GET") not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            raise ValueError("Unsupported method")
        if not probe.get("expected") or any(
            not isinstance(code, int) or code < 200 or code >= 500
            for code in probe["expected"]
        ):
            raise ValueError(
                "Each probe needs explicit expected status codes below 500"
            )
        dependency = probe.get("requires")
        if dependency is not None and (
            not isinstance(dependency, int)
            or not 0 <= dependency < index
            or "identity" not in probes[dependency]
        ):
            raise ValueError("requires must refer to an earlier identity baseline")
        headers = probe.get("headers", {})
        if any(
            k.lower()
            not in {"cookie", "x-csrf-token", "origin", "content-type", "authorization"}
            for k in headers
        ):
            raise ValueError("Unsupported header")
        if any(
            not isinstance(v, str)
            or len(v) > 16384
            or any(ord(c) < 32 or ord(c) > 126 for c in v)
            for v in headers.values()
        ):
            raise ValueError("Invalid header")
        if any(k.lower() in {"cookie", "authorization"} for k in headers):
            if "identity" in probe:
                if (
                    probe.get("method", "GET") != "GET"
                    or not path.endswith("/v1/me")
                    or probe["expected"] != [200]
                ):
                    raise ValueError("Identity baseline must GET /v1/me and expect 200")
                if set(probe["identity"]) != {"user", "effective_role"}:
                    raise ValueError("Identity baseline needs user and effective_role")
            else:
                dependency = probe.get("requires")
                if (
                    not isinstance(dependency, int)
                    or not 0 <= dependency < index
                    or "identity" not in probes[dependency]
                ):
                    raise ValueError(
                        "Authenticated probes need an earlier identity baseline"
                    )
                baseline_headers = probes[dependency].get("headers", {})

                def auth(h):
                    return {
                        k.lower(): v
                        for k, v in h.items()
                        if k.lower() in {"cookie", "authorization"}
                    }

                if auth(headers) != auth(baseline_headers):
                    raise ValueError(
                        "Probe credentials must match its identity baseline"
                    )
        if len(json.dumps(probe.get("json", {})).encode()) > 16384:
            raise ValueError("Probe body exceeds 16 KiB")
    return probes


def http_probes(origin, probes, allow_writes, ca_file=None):
    results = []
    deadline = time.monotonic() + 120
    for index, probe in enumerate(probes):
        if time.monotonic() >= deadline:
            results.append(
                {"case": index, "result": "incomplete", "reason": "time budget"}
            )
            break
        dependency = probe.get("requires")
        if dependency is not None and results[dependency]["result"] != "pass":
            results.append(
                {
                    "case": index,
                    "result": "skipped",
                    "reason": "identity baseline failed",
                }
            )
            continue
        method = probe.get("method", "GET")
        if method != "GET" and not allow_writes:
            results.append(
                {"case": index, "result": "skipped", "reason": "writes disabled"}
            )
            continue
        connection = None
        try:
            timeout = min(3, deadline - time.monotonic())
            if origin.scheme == "https":
                connection = http.client.HTTPSConnection(
                    origin.hostname,
                    origin.port,
                    timeout=timeout,
                    context=ssl.create_default_context(cafile=ca_file),
                )
            else:
                connection = http.client.HTTPConnection(
                    origin.hostname, origin.port, timeout=timeout
                )
            headers = dict(probe.get("headers", {}))
            body = None
            if "json" in probe:
                body = json.dumps(probe["json"], allow_nan=False).encode()
                headers.setdefault("Content-Type", "application/json")
            connection.request(method, probe["path"], body=body, headers=headers)
            response = connection.getresponse()
            # Ordinary probes inspect status only; identity baselines also inspect bounded JSON.
            result = "pass" if response.status in probe["expected"] else "finding"
            if "identity" in probe and response.status == 200:
                raw = bytearray()
                read_deadline = min(deadline, time.monotonic() + 3)
                while len(raw) <= 65536:
                    if time.monotonic() >= read_deadline:
                        raise TimeoutError()
                    chunk = response.read1(min(8192, 65537 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > 65536:
                    raise ValueError("Identity response too large")
                identity = json.loads(raw)
                if any(identity.get(k) != v for k, v in probe["identity"].items()):
                    result = "finding"
            results.append({"case": index, "result": result, "status": response.status})
        except Exception as exc:
            results.append(
                {"case": index, "result": "incomplete", "type": type(exc).__name__}
            )
        finally:
            if connection:
                connection.close()
        time.sleep(0.25)
    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="HTTP manifests specify probes with path, method, expected status list, optional headers and json. Include a successful authenticated baseline before denied-role/object checks. Status checks alone cannot prove absence of vulnerabilities. Use disposable fixtures: even GET endpoints may have side effects. Reports contain case indices, never cookies or response bodies. Pair with Schemathesis and authenticated browser tests.",
    )
    parser.add_argument("mode", choices=("offline", "http"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument(
        "--origin", help="Literal loopback origin, e.g. http://127.0.0.1:8000"
    )
    parser.add_argument(
        "--manifest", help="JSON file, chmod 600; use synthetic dev credentials"
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Permit explicit write probes against disposable data",
    )
    parser.add_argument(
        "--ca-file",
        help="CA bundle for local HTTPS; hostname verification remains enabled",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="New output file (created mode 0600; never overwritten)",
    )
    args = parser.parse_args()
    if not 1 <= args.cases <= 10000:
        parser.error("cases must be 1–10000")
    try:
        if args.mode == "offline":
            results = offline(args.seed, args.cases)
        else:
            if not args.origin or not args.manifest:
                parser.error("HTTP mode requires origin and manifest")
            results = http_probes(
                target(args.origin),
                load_manifest(args.manifest),
                args.allow_writes,
                args.ca_file,
            )
        report = {
            "version": 1,
            "mode": args.mode,
            "seed": args.seed,
            "results": results,
        }
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(report, out, indent=2)
            out.write("\n")
    except (ValueError, OSError, KeyError, TypeError):
        parser.error(
            "Invalid configuration or inaccessible file; check origin, manifest and new report path"
        )
    counts = {
        name: sum(r["result"] == name for r in results)
        for name in ("pass", "finding", "skipped", "incomplete")
    }
    print(json.dumps(counts))
    return (
        1
        if counts["finding"]
        else 2
        if counts["skipped"] or counts["incomplete"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
