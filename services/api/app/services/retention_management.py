"""Evidence retention application operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import evidence_retention as retention
from app.services.errors import DomainError


class PolicyPayload(BaseModel):
    mode: Literal["disabled", "age", "count"] = "disabled"
    value: int | None = Field(default=None, strict=True, ge=1, le=1_000_000_000)

    @model_validator(mode="after")
    def validate_limit(self):
        if self.mode == "disabled":
            if self.value is not None:
                raise ValueError("Disabled retention must have no limit")
        elif self.value is None:
            raise ValueError("A positive limit is required")
        if self.mode == "age" and self.value > 36500:
            raise ValueError("Age must be between 1 and 36500 days")
        return self


class PreviewPayload(PolicyPayload):
    pass


class PurgePayload(BaseModel):
    mode: Literal["policy", "all"]
    confirmation: Literal["PURGE EVIDENCE"]


def get_status(*, db: Session):
    p = retention.policy(db)
    jobs = [
        dict(r)
        for r in db.execute(
            text("""SELECT * FROM evidence_purge_jobs
        ORDER BY created_at DESC LIMIT 10""")
        ).mappings()
    ]
    cleanup = dict(
        db.execute(
            text("""SELECT count(*) AS pending_objects,
        count(*) FILTER (WHERE last_error IS NOT NULL) AS waiting_objects,
        min(next_attempt_at) AS next_attempt_at FROM evidence_object_cleanup""")
        )
        .mappings()
        .one()
    )
    cleanup["errors"] = list(
        db.execute(
            text("""SELECT DISTINCT last_error FROM evidence_object_cleanup
        WHERE last_error IS NOT NULL LIMIT 5""")
        ).scalars()
    )
    counts = retention.preview(db, "disabled", None)
    from app.services.ingestion_pause import purge_pause_status

    return {
        "policy": p,
        "jobs": jobs,
        "storage": cleanup,
        "ingestion_pause": purge_pause_status(db),
        **counts,
    }


def preview(*, payload: PreviewPayload, db: Session):
    return retention.preview(db, payload.mode, payload.value)


def preview_all(*, db: Session):
    return retention.preview(db, "all", None)


def set_policy(*, payload: PolicyPayload, db: Session, user):
    retention.lock(db)
    db.execute(
        text("""UPDATE evidence_retention_policy SET mode=:mode,value=:value,
        updated_at=:now,updated_by=:user WHERE id=1"""),
        {
            "mode": payload.mode,
            "value": payload.value,
            "now": retention.now(),
            "user": user.username,
        },
    )
    # Changing a policy stops its previous snapshot; manual purge is independent.
    db.execute(
        text("""UPDATE evidence_purge_jobs SET status='cancelled',updated_at=:now
        WHERE automatic AND status IN ('queued','running')"""),
        {"now": retention.now()},
    )
    db.commit()
    return retention.policy(db)


def purge(*, payload: PurgePayload, db: Session, user):
    retention.lock(db)
    p = retention.policy(db)
    mode, value = ("all", None) if payload.mode == "all" else (p["mode"], p["value"])
    if mode == "disabled":
        raise DomainError(400, "Save an age or count policy first")
    job_id, created = retention.enqueue(db, mode, value, False, user.username)
    if not created:
        raise DomainError(
            409, "A purge is already queued or running; wait for it or cancel it first"
        )
    db.commit()
    # Beat picks this up even if the broker is temporarily unavailable.
    return {"job_id": job_id, "status": "queued"}


def cancel(*, db: Session):
    retention.lock(db)
    db.execute(
        text("""UPDATE evidence_purge_jobs SET status='cancelled',updated_at=:now
        WHERE status IN ('queued','running')"""),
        {"now": retention.now()},
    )
    db.commit()
    return {"ok": True}


def retry_storage(*, db: Session):
    db.execute(
        text("UPDATE evidence_object_cleanup SET next_attempt_at=:now"),
        {"now": retention.now()},
    )
    db.commit()
    return {"ok": True}
