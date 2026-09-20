from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy.orm import Session

from app.ingest.common import fingerprint, store_event_with_artifact


def add_diary_entry(
    db: Session,
    author: str | None,
    summary: str,
    details: dict | None = None,
    links: list[dict] | None = None,
    *,
    timestamp: datetime | None = None,
) -> dict:
    """Insert a manual diary entry event.

    The author is expected to come from upstream auth (e.g. Vouch) and should not
    be trusted from the browser.
    """
    ts = timestamp or datetime.now(timezone.utc)
    payload = {"summary": summary, "details": details or {}, "author": author}
    if links:
        payload["links"] = links
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    ext_id = fingerprint(["diary", author or "unknown", summary], payload_bytes)

    key = f"diary/{ts.date().isoformat()}/{ext_id}.json"
    return store_event_with_artifact(
        db,
        timestamp=ts,
        source="diary",
        system="manual",
        actor=author,
        action="diary_entry",
        outcome="info",
        severity=2,
        summary=f"diary: {summary}",
        raw_pointer={"diary": {"author": author}},
        normalized_payload=payload,
        external_id=ext_id,
        artifact_kind="diary_entry",
        artifact_bytes=payload_bytes,
        artifact_content_type="application/json",
        artifact_key=key,
        captured_by="keen:diary",
    )
