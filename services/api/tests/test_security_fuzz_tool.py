"""The local harness must constrain destinations and preserve credentials."""

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

spec = importlib.util.spec_from_file_location(
    "keen_security_fuzz",
    Path(__file__).resolve().parents[3] / "tools/security_fuzz/run.py",
)
fuzz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fuzz)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org",
        "http://localhost",
        "http://169.254.169.254",
        "http://127.0.0.1@evil.example",
        "http://127.0.0.1/api",
        "ftp://127.0.0.1",
    ],
)
def test_fuzzer_rejects_non_loopback_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        fuzz.target(url)


def test_offline_mutations_are_reproducible():
    result = fuzz.offline(47, 100)
    assert result == fuzz.offline(47, 100)
    assert all(row["result"] == "pass" for row in result)


def test_http_harness_does_not_follow_redirect_or_write_by_default(tmp_path):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = fuzz.target(f"http://127.0.0.1:{server.server_port}")
        probes = [
            {"path": "/", "expected": [200]},
            {"path": "/write", "method": "POST", "expected": [200]},
        ]
        result = fuzz.http_probes(origin, probes, False)
        assert result[0] == {"case": 0, "result": "finding", "status": 302}
        assert result[1]["result"] == "skipped"
        assert paths == ["/"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_manifest_permissions_and_header_injection(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "probes": [
                    {
                        "path": "/",
                        "expected": [200],
                        "headers": {"Cookie": "secret\r\nInjected: yes"},
                    }
                ]
            }
        )
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="Invalid header"):
        fuzz.load_manifest(path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        fuzz.load_manifest(path)


def test_authenticated_checks_require_matching_baseline(tmp_path):
    path = tmp_path / "manifest.json"
    baseline = {
        "path": "/v1/me",
        "expected": [200],
        "headers": {"Cookie": "synthetic"},
        "identity": {"user": "reader", "effective_role": "normal"},
    }
    probe = {"path": "/v1/users", "expected": [403], "headers": {"Cookie": "synthetic"}}
    path.write_text(json.dumps({"probes": [baseline, probe]}))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="earlier identity"):
        fuzz.load_manifest(path)
    probe["requires"] = 0
    path.write_text(json.dumps({"probes": [baseline, probe]}))
    assert len(fuzz.load_manifest(path)) == 2
    probe["headers"]["Cookie"] = "different"
    path.write_text(json.dumps({"probes": [baseline, probe]}))
    with pytest.raises(ValueError, match="credentials must match"):
        fuzz.load_manifest(path)


def test_failed_identity_baseline_skips_dependent_checks():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"user":"unexpected","effective_role":"admin"}')

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        probes = [
            {
                "path": "/v1/me",
                "expected": [200],
                "identity": {"user": "reader", "effective_role": "normal"},
            },
            {"path": "/v1/users", "expected": [403], "requires": 0},
        ]
        results = fuzz.http_probes(
            fuzz.target(f"http://127.0.0.1:{server.server_port}"), probes, False
        )
        assert results[0]["result"] == "finding"
        assert results[1]["result"] == "skipped"
        assert paths == ["/v1/me"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
