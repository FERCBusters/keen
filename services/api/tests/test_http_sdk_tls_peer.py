"""Real local TLS peers: SDKs must verify identity before sending credentials."""

import json
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
from app.ingest.cloudwatch_logs import _cloudwatch_client
from app.ingest.google_workspace import _HTTPSOnlyHttp
from botocore.exceptions import SSLError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.mark.parametrize("provider", ["google", "aws"])
@pytest.mark.parametrize(
    "case", ["matching", "wrong-name", "untrusted", "expired", "redirect"]
)
def test_sdk_tls_rejects_before_authentication(tmp_path, monkeypatch, provider, case):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = "wrong.example" if case == "wrong-name" else "localhost"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if case == "expired" else 1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(self.headers.get("Authorization"))
            self.send_response(302 if case == "redirect" else 200)
            if case == "redirect":
                self.send_header("Location", "http://127.0.0.1:1/plaintext-forbidden")
            self.send_header("Content-Type", "application/x-amz-json-1.1")
            self.end_headers()
            self.wfile.write(json.dumps({"logGroups": []}).encode())

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://localhost:{server.server_port}"
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("AWS_ENDPOINT_URL", url)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.delenv("AWS_CA_BUNDLE", raising=False)
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)
    if case != "untrusted":
        monkeypatch.setenv("AWS_CA_BUNDLE", str(certfile))
    client = None
    try:
        if provider == "google":
            client = _HTTPSOnlyHttp(
                ca_certs=str(certfile) if case != "untrusted" else None
            )

            def request():
                return client.request(
                    url, headers={"Authorization": "Bearer synthetic-secret"}
                )
        else:
            client = _cloudwatch_client("eu-west-1")
            request = client.describe_log_groups
        if case in {"wrong-name", "untrusted", "expired"}:
            with pytest.raises((ssl.SSLError, SSLError)):
                request()
            assert received == []
        elif case == "redirect" and provider == "aws":
            from botocore.exceptions import ClientError

            with pytest.raises(ClientError):
                request()
            assert len(received) == 1
        else:
            response = request()
            if provider == "google":
                assert response[0].status == (302 if case == "redirect" else 200)
            assert len(received) == 1 and received[0]
    finally:
        if client:
            client.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_google_rejects_plaintext_before_network():
    with pytest.raises(ValueError, match="HTTPS"):
        _HTTPSOnlyHttp().request(
            "http://example.test", headers={"Authorization": "synthetic-secret"}
        )
