"""Add stored evidence files to a staging golden dataset.

Run inside the API container after the demo seeder:

    docker compose exec -T keen-api python - < tools/staging/golden_extras.py

The seeder creates records without stored files. These events carry real
artifacts (text, JSON, PDF, HTML and CSV) so downloads, previews, masking and
attachment headers have something to serve. All content is synthetic. Re-running
is harmless: events are de-duplicated by their external IDs.
"""

import json
from datetime import timedelta

from app.core.datetime_utils import utc_now_naive
from app.db.session import SessionLocal
from app.ingest.common import store_event_with_artifact


def minimal_pdf(text):
    """A one-page PDF with a line of text, small enough to build by hand."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return body


EVIDENCE = [
    {
        "key": "change-approval",
        "action": "change.approved",
        "summary": "Golden: production change CHG-1042 approved by the change board",
        "content_type": "text/plain; charset=utf-8",
        "data": b"Change CHG-1042\nApproved by: change board\nRollback plan: documented\n",
        "suffix": "txt",
    },
    {
        "key": "deploy-record",
        "action": "deployment.completed",
        "summary": "Golden: deployment record with nested structured fields",
        "content_type": "application/json",
        # The password value is synthetic; redaction should replace it on storage.
        "data": json.dumps(
            {
                "service": "billing-api",
                "version": "4.2.0",
                "approvals": [{"user": "reviewer-1", "role": "lead"}],
                "settings": {
                    "password": "golden-not-a-real-secret",
                    "region": "eu-west-2",
                },
            }
        ).encode(),
        "suffix": "json",
    },
    {
        "key": "policy-pdf",
        "action": "document.reviewed",
        "summary": "Golden: access control policy review (PDF)",
        "content_type": "application/pdf",
        "data": minimal_pdf("Access control policy - reviewed"),
        "suffix": "pdf",
    },
    {
        "key": "report-html",
        "action": "report.exported",
        "summary": "Golden: exported HTML report (must download, never render)",
        "content_type": "text/html; charset=utf-8",
        "data": b"<!doctype html><title>Report</title><p>Quarterly access review</p>",
        "suffix": "html",
    },
    {
        "key": "access-csv",
        "action": "access.reviewed",
        "summary": "Golden: user access review export (CSV)",
        "content_type": "text/csv",
        "data": b'user,role,decision\nreviewer-1,admin,keep\n=HYPERLINK("https://example.org"),viewer,remove\n',
        "suffix": "csv",
    },
]


def main():
    now = utc_now_naive()
    with SessionLocal() as db:
        for index, item in enumerate(EVIDENCE):
            result = store_event_with_artifact(
                db,
                timestamp=now - timedelta(days=index + 1),
                source="staging-golden",
                system="golden-dataset",
                actor="golden-seeder",
                action=item["action"],
                outcome="success",
                severity=3,
                summary=item["summary"],
                raw_pointer={"golden": item["key"]},
                normalized_payload={"golden": item["key"], "kind": item["suffix"]},
                external_id="staging-golden:" + item["key"],
                artifact_kind="golden_" + item["suffix"],
                artifact_bytes=item["data"],
                artifact_content_type=item["content_type"],
                artifact_key=f"staging-golden/{item['key']}.{item['suffix']}",
                captured_by="staging-golden",
            )
            state = "already present" if result.get("deduped") else "stored"
            print(f"golden evidence {item['key']}: {state}")


if __name__ == "__main__":
    main()
