"""Uploaded active content is served as inert downloads with sanitised names."""

from __future__ import annotations

import pytest

HOSTILE_FILES = [
    ("x.html", b"<html><script>window.__keenE2E=1</script></html>", "text/html"),
    (
        "x.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        "image/svg+xml",
    ),
    ("x.png", b"<html><script>alert(1)</script>", "image/png"),
    ('evil"\r\nX-Injected: 1.pdf', b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    ("../../../../etc/passwd", b"plain text", "text/plain"),
    ("nul\x00name.txt", b"plain text", "text/plain"),
    ("\u202egnp.exe", b"plain text", "text/plain"),
]


def _check_download(response):
    problems = []
    disposition = response.headers.get("content-disposition", "")
    if not disposition.lower().startswith("attachment"):
        problems.append("not an attachment")
    if response.headers.get("x-content-type-options", "").lower() != "nosniff":
        problems.append("missing nosniff")
    if "x-injected" in {k.lower() for k in response.headers}:
        problems.append("header injection")
    if ".." in disposition or "/" in disposition.split("filename", 1)[-1]:
        problems.append("path in filename")
    return problems


def test_isms_document_uploads(admin, seed):
    document = seed.ids["document"]
    findings = []
    for name, data, content_type in HOSTILE_FILES:
        upload = admin.post(
            f"/api/v1/isms/documents/{document}/file",
            files={"file": (name, data, content_type)},
        )
        if upload.status_code >= 500:
            findings.append((name, "upload", upload.status_code))
            continue
        if upload.status_code not in (200, 201):
            continue
        download = admin.get(f"/api/v1/isms/documents/{document}/file")
        problems = _check_download(download)
        if problems:
            findings.append((name, problems))
    assert not findings, findings


def test_audit_report_uploads(admin, seed):
    audit = seed.ids["audit"]
    findings = []
    for name, data, content_type in HOSTILE_FILES:
        upload = admin.post(
            f"/api/v1/audits/{audit}/report", files={"file": (name, data, content_type)}
        )
        if upload.status_code >= 500:
            findings.append((name, "upload", upload.status_code))
            continue
        if upload.status_code not in (200, 201):
            continue
        problems = _check_download(admin.get(f"/api/v1/audits/{audit}/report"))
        if problems:
            findings.append((name, problems))
    assert not findings, findings


def test_question_attachments_validate_content(accounts, seed, evidence_writes):
    if not evidence_writes:
        pytest.skip(
            "Needs KEEN_E2E_ALLOW_EVIDENCE_WRITES=1 for a synthetic diary event"
        )
    session = accounts("questioner")
    event = seed.ids["event"]
    spoofed = session.post(
        f"/api/v1/events/{event}/questions",
        data={"body": "e2e attachment check"},
        files={
            "attachments": ("x.png", b"<html><script>alert(1)</script>", "image/png")
        },
    )
    assert spoofed.status_code == 400, spoofed.status_code
    detail = session.get(f"/api/v1/events/{event}").json()
    assert all(
        a.get("kind") != "question_attachment" for a in detail.get("artifacts", [])
    )


def test_uploaded_files_cannot_be_framed_or_rendered_inline(
    page_as, admin, seed, target
):
    """Opening a hostile upload in the browser must download it, never run it."""
    document = seed.ids["document"]
    admin.post(
        f"/api/v1/isms/documents/{document}/file",
        files={"file": ("x.html", HOSTILE_FILES[0][1], "text/html")},
    )
    page = page_as(admin)
    executed = []
    page.on("dialog", lambda d: (executed.append(d.type), d.dismiss()))
    try:
        with page.expect_download(timeout=15000):
            page.evaluate(
                "url => { const a = document.createElement('a'); a.href = url; document.body.appendChild(a); a.click(); }",
                f"{target.origin}/api/v1/isms/documents/{document}/file",
            )
    except Exception:
        pass
    assert page.evaluate("() => window.__keenE2E === 1") is False
    assert not executed


def test_multipart_text_fields_with_control_characters(accounts, seed, evidence_writes):
    """Form text carrying NUL or other control characters is refused or stored, never a server error."""
    if not evidence_writes:
        pytest.skip(
            "Needs KEEN_E2E_ALLOW_EVIDENCE_WRITES=1 for a synthetic diary event"
        )
    session = accounts("questioner")
    event = seed.ids["event"]
    response = session.post(
        f"/api/v1/events/{event}/questions", data={"body": "e2e \x00 \x1b[31m question"}
    )
    assert response.status_code < 500, response.status_code
