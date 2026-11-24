"""Connection-isolated IMAP polling; transport never receives database credentials."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from mosp.core.datetime_utils import utc_now_naive
from mosp.core.task_deadline import TIME_LIMITS
from mosp.security.json_shape import decode_json
from mosp.security.redaction import mask_mail_address_headers

from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, store_event_with_artifact
from app.ingest.connections import (
    connection_runs,
    current_connection,
    namespace,
    settings,
)
from app.ingest.imap_support.config import configuration
from app.ingest.registry import IMAP_QUEUE, Source, register
from app.services.ingestion_pause import pausable

WORKER_TIMEOUT = 55
# A message that ends this many runs in a row is recorded without its content
# and passed, so one message cannot hold back the mail after it.
FAILED_MESSAGE_RUNS = 3


# Messages recorded by UID alone, and why.
OMITTED = {
    "message_exceeds_limits": "its headers or structure exceed Keen's limits",
    "message_failed_repeatedly": f"reading it failed {FAILED_MESSAGE_RUNS} runs in a row",
}


class MessageFailed(ValueError):
    """A run ended while reading message ``uid``."""

    def __init__(self, uid: int):
        super().__init__(
            "IMAP collection failed a connection, protocol or resource check"
        )
        self.uid = uid


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
            uid = _failed_uid(output) if proc.returncode else None
            if uid is not None:
                raise MessageFailed(uid)
            raise ValueError(
                "IMAP collection failed a connection, protocol or resource check"
            )
    return json.loads(output)


def _failed_uid(output: bytes) -> int | None:
    """The message a failed worker was reading, from its fixed error report."""
    if len(output) > 256:
        return None
    try:
        uid = decode_json(output).get("failed_uid")
    except (ValueError, AttributeError):
        return None
    return uid if type(uid) is int and uid > 0 else None


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
    if meta.get("last_poll_at"):
        due = datetime.fromisoformat(meta["last_poll_at"]) + timedelta(
            minutes=config["poll_interval_minutes"]
        )
        if now < due:
            return [
                {
                    "skipped": True,
                    "reason": "IMAP collection interval",
                    "next_poll_at": due.isoformat(),
                }
            ]
    state = (
        {"uidvalidity": meta["uidvalidity"], "last_uid": meta["last_uid"]}
        if "uidvalidity" in meta
        else None
    )
    # Messages whose reading ended FAILED_MESSAGE_RUNS runs in a row are
    # recorded by UID alone and passed, however many there are in the window.
    skip_uids = [
        uid
        for uid in meta.get("skip_uids") or []
        if type(uid) is int and state and uid > state["last_uid"]
    ][:10]
    if state and skip_uids:
        state["skip_uids"] = skip_uids
    # Persist admission under the connection run lock before any network work.
    # Failures in collection or storage must not bypass the polling interval.
    meta["last_poll_at"] = now.isoformat()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
        db.add(cursor)
    cursor.meta = dict(meta)
    db.commit()  # No database transaction spans the remote wait.
    try:
        result = fetch({**config, "password": password}, state)
    except TIME_LIMITS:
        # The task's time limit ends the run; it is no failed poll to back off.
        raise
    except Exception as exc:
        db.rollback()
        cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
        if cursor is None:
            cursor = IngestionCursor(name=name, meta={})
            db.add(cursor)
        failures = min(int(meta.get("failures", 0)) + 1, 8)
        failed = {}
        error = "IMAP collection failed a connection, protocol or resource check"
        uid = exc.uid if isinstance(exc, MessageFailed) else None
        if (
            state
            and uid is not None
            and state["last_uid"] < uid <= state["last_uid"] + 100
        ):
            # Runs that end at the same message count towards passing it.
            runs = meta.get("failed_runs", 0) if meta.get("failed_uid") == uid else 0
            failed = {"failed_uid": uid, "failed_runs": runs + 1}
            if runs + 1 >= FAILED_MESSAGE_RUNS and uid not in skip_uids:
                failed = {}
                skip_uids = [*skip_uids, uid][-10:]
            error += f" while reading message UID {uid}"
        kept = ("failed_uid", "failed_runs", "skip_uids", "retry_after")
        # A run that ended at a message moves its count on; the next is due
        # at the usual interval, so a few such messages cost a few polls.
        counted = bool(failed) or uid in skip_uids
        cursor.meta = {
            **{k: v for k, v in meta.items() if k not in kept},
            **failed,
            **({"skip_uids": skip_uids} if skip_uids else {}),
            "failures": int(meta.get("failures", 0)) if counted else failures,
            **(
                {}
                if counted
                else {
                    "retry_after": (
                        now + timedelta(seconds=min(3600, 60 * 2**failures))
                    ).isoformat()
                }
            ),
        }
        db.commit()
        return [{"ok": False, "error": error + "; retry is delayed"}]
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

    def read(item):
        item["headers"] = mask_mail_address_headers(item["headers"])
        key = f"{identity}:{result['uidvalidity']}:{item['uid']}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        payload = {
            "mail": item,
            "mailbox": config["folder"],
            "uidvalidity": result["uidvalidity"],
        }
        artifact = json.dumps(payload, ensure_ascii=True).encode()
        # Worker redacts before character truncation; the shared persistence path also redacts.
        return dict(
            timestamp=now,
            source="imap",
            system=config["host"],
            actor=item["headers"]["from"] or None,
            action="mail.received",
            outcome="info",
            severity=3,
            summary=(
                f"Email UID {item['uid']} recorded without its content: "
                + OMITTED[item["body_omitted_reason"]]
                if item.get("body_omitted_reason") in OMITTED
                else "Email: " + (item["headers"]["subject"] or "(no subject)")
            )[:512],
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

    reader = RecordReader()
    for event in reader.each(result["items"], read):
        stored = store_event_with_artifact(db, **event)
        created += not stored.get("deduped", False)
    skip_uids = [uid for uid in skip_uids if uid > result["last_uid"]]
    cursor.meta = {
        "uidvalidity": result["uidvalidity"],
        "last_uid": result["last_uid"],
        "failures": 0,
        "last_poll_at": meta["last_poll_at"],
        **({"skip_uids": skip_uids} if skip_uids else {}),
    }
    cursor.last_ts = now
    db.commit()
    return [
        {
            "ok": True,
            "created_events": created,
            "invalid_records": reader.invalid,
            "initialised": result.get("initialised", False),
            "note": "First run starts from current mailbox position; earlier mail is not imported"
            if result.get("initialised")
            else "Read-only mailbox polling completed",
        }
    ]


# Mail is collected by its own worker; keen.dispatch_imap starts the runs.
register(
    Source(
        name="imap",
        module=__name__,
        queue=IMAP_QUEUE,
        public_fields=(
            "host",
            "port",
            "username",
            "folder",
            "fetch_body",
            "body_max_chars",
            "poll_interval_minutes",
        ),
        secret_fields=("password",),
        demo=False,
    )
)
