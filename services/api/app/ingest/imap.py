"""Connection-isolated IMAP polling; transport never receives database credentials."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from app.core.datetime_utils import utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import store_event_with_artifact
from app.ingest.connections import (
    connection_runs,
    current_connection,
    namespace,
    settings,
)
from app.ingest.imap_support.config import configuration
from app.services.ingestion_pause import pausable

WORKER_TIMEOUT = 55


def fetch(config, cursor):
    request = json.dumps(
        {
            "configuration": config,
            "cursor": cursor,
            "allowed_cidrs": settings.ingestion_allowed_cidrs,
        }
    ).encode()
    if len(request) > 32768:
        raise ValueError("IMAP configuration exceeds limit")
    # communicate drains bounded child output; timeout kills and reaps blocked DNS/TLS/reads.
    with subprocess.Popen(
        [sys.executable, "-m", "app.ingest.imap_support.worker"],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": os.defpath,
            "PYTHONIOENCODING": "utf-8",
            **{
                key: os.environ[key]
                for key in ("KEEN_SENSITIVE_KEYS", "KEEN_SENSITIVE_KEYS_MODE")
                if key in os.environ
            },
        },
        close_fds=True,
    ) as proc:
        try:
            output, _ = proc.communicate(request, timeout=WORKER_TIMEOUT)
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        if proc.returncode or len(output) > 5 * 1024 * 1024:
            raise ValueError(
                "IMAP collection failed a connection, protocol or resource check"
            )
    return json.loads(output)


@pausable("imap")
@connection_runs("imap")
def ingest_imap_all(db):
    connection = current_connection()
    if connection.managed_by == "environment":
        return [{"skipped": True, "reason": "Create a named IMAP connection"}]
    if settings.demo_mode:
        return [{"skipped": True, "reason": "IMAP is disabled in demo environments"}]
    config = configuration(connection.configuration)
    password = connection.credentials.get("password", "")
    if (
        not password
        or len(password) > 4096
        or any(ord(c) < 32 or ord(c) > 126 for c in password)
    ):
        return [
            {
                "ok": False,
                "error": "IMAP requires a printable ASCII password or app password (up to 4096 characters)",
            }
        ]
    # Endpoint/account/folder changes start a fresh cursor; credentials never enter it.
    identity = hashlib.sha256(
        json.dumps([config[k] for k in ("host", "port", "username", "folder")]).encode()
    ).hexdigest()
    name = namespace("imap:" + identity)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    meta = dict(cursor.meta or {}) if cursor else {}
    now = utc_now_naive()
    if meta.get("reset_required"):
        return [
            {
                "ok": False,
                "error": "Mailbox UIDVALIDITY changed. Create a new connection to establish a reviewed baseline.",
            }
        ]
    if meta.get("retry_after") and now.isoformat() < meta["retry_after"]:
        return [{"skipped": True, "reason": "IMAP retry backoff"}]
    state = (
        {"uidvalidity": meta["uidvalidity"], "last_uid": meta["last_uid"]}
        if "uidvalidity" in meta
        else None
    )
    # Release the caller's database transaction before waiting for the remote server.
    db.commit()
    try:
        result = fetch({**config, "password": password}, state)
    except Exception:
        db.rollback()
        cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
        if cursor is None:
            cursor = IngestionCursor(name=name, meta={})
            db.add(cursor)
        failures = min(int(meta.get("failures", 0)) + 1, 8)
        cursor.meta = {
            **meta,
            "failures": failures,
            "retry_after": (
                now + timedelta(seconds=min(3600, 60 * 2**failures))
            ).isoformat(),
        }
        db.commit()
        return [
            {
                "ok": False,
                "error": "IMAP collection failed a connection, protocol or resource check; retry is delayed",
            }
        ]
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
        db.add(cursor)
    if result.get("reset_required"):
        cursor.meta = {**meta, "reset_required": True}
        db.commit()
        return [
            {
                "ok": False,
                "error": "Mailbox UIDVALIDITY changed. Collection paused; create a new connection to establish a reviewed baseline.",
            }
        ]
    created = 0
    for item in result["items"]:
        key = f"{identity}:{result['uidvalidity']}:{item['uid']}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        payload = {
            "mail": item,
            "mailbox": config["folder"],
            "uidvalidity": result["uidvalidity"],
        }
        artifact = json.dumps(payload, ensure_ascii=True).encode()
        # Worker redacts before character truncation; the shared persistence path also redacts.
        stored = store_event_with_artifact(
            db,
            timestamp=now,
            source="imap",
            system=config["host"],
            actor=item["headers"]["from"] or None,
            action="mail.received",
            outcome="info",
            severity=3,
            summary=("Email: " + (item["headers"]["subject"] or "(no subject)"))[:512],
            raw_pointer={
                "imap": {
                    "uid": item["uid"],
                    "uidvalidity": result["uidvalidity"],
                    "folder": config["folder"],
                }
            },
            normalized_payload=payload,
            external_id=digest,
            artifact_kind="imap_message",
            artifact_bytes=artifact,
            artifact_content_type="application/json",
            artifact_key="imap/" + digest + ".json",
            captured_by="keen:imap",
            commit=False,
        )
        created += not stored.get("deduped", False)
    cursor.meta = {
        "uidvalidity": result["uidvalidity"],
        "last_uid": result["last_uid"],
        "failures": 0,
    }
    cursor.last_ts = now
    db.commit()
    return [
        {
            "ok": True,
            "created_events": created,
            "initialised": result.get("initialised", False),
            "note": "First run starts from current mailbox position; earlier mail is not imported"
            if result.get("initialised")
            else "Read-only mailbox polling completed",
        }
    ]
