"""Evidence retention application operations."""

from __future__ import annotations

from typing import Literal
from datetime import datetime

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


class OverridePayload(PolicyPayload):
    scope: Literal["source", "rule"]
    key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")

    @model_validator(mode="after")
    def validate_key(self):
        if self.key != self.key.strip():
            raise ValueError(
                "Use an exact source type or rule ID without surrounding whitespace"
            )
        return self


class SettingsPayload(PolicyPayload):
    expected_updated_at: datetime | None = None
    # Omission preserves overrides for existing API clients.
    overrides: list[OverridePayload] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def unique_overrides(self):
        if self.overrides is not None:
            keys = [(item.scope, item.key) for item in self.overrides]
            if len(keys) != len(set(keys)):
                raise ValueError("Each source type or rule may have only one override")
        return self


class PreviewPayload(SettingsPayload):
    pass


class PurgePayload(BaseModel):
    mode: Literal["policy", "all"]
    confirmation: Literal["PURGE EVIDENCE"]


def override_choices(db: Session):
    """Exact event source IDs and current rule IDs; never connection display names."""
    from app.core.config import settings
    from app.core.managed_configuration import load_document
    from app.core.source_meta import SOURCE_LABELS
    from app.db.models import IntegrationCollector, SourceConnection
    from app.services.managed_configurations import _normalized_rules

    sources = {key: label for key, label in SOURCE_LABELS.items() if key != "webhooks"}
    for source in db.execute(text("SELECT DISTINCT source FROM events")).scalars():
        sources.setdefault(source, source)
    providers = (
        load_document("webhooks", settings.webhooks_path, db=db).get("providers") or {}
    )
    for provider in providers:
        sources["webhook:" + provider] = "Webhook: " + provider
    for connection in db.query(SourceConnection).filter_by(source="webhooks"):
        provider = (connection.configuration or {}).get("provider")
        if provider:
            sources["webhook:" + provider] = "Webhook: " + provider
    for collector in db.query(IntegrationCollector):
        sources["integration:" + collector.id] = collector.name
    rules = _normalized_rules(db)
    return {
        "source": [
            {"key": key, "label": label} for key, label in sorted(sources.items())
        ],
        "rule": [
            {"key": rule["id"], "label": rule.get("description") or rule["id"]}
            for rule in sorted(rules, key=lambda rule: rule["id"])
        ],
    }


def validate_override_choices(payload, db):
    if not payload.overrides:
        return
    choices = override_choices(db)
    allowed = {
        (scope, item["key"]) for scope, items in choices.items() for item in items
    }
    # Historical overrides retain their identity after a rule/source is removed.
    # They may be edited or removed, but cannot be newly invented by a caller.
    allowed.update(
        tuple(row)
        for row in db.execute(
            text("SELECT scope,key FROM evidence_retention_overrides")
        )
    )
    if any((item.scope, item.key) not in allowed for item in payload.overrides):
        raise DomainError(
            422,
            "Choose an existing source type or mapping rule. Reload the page if the available values have changed.",
        )


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
    counts = retention.preview(db, "disabled", None, overrides=[])
    from app.services.ingestion_pause import purge_pause_status

    return {
        "policy": p,
        "choices": override_choices(db),
        "overrides": [
            dict(row)
            for row in db.execute(
                text(
                    "SELECT scope,key,mode,value FROM evidence_retention_overrides ORDER BY scope,key"
                )
            ).mappings()
        ],
        "jobs": jobs,
        "storage": cleanup,
        "ingestion_pause": purge_pause_status(db),
        **counts,
    }


def preview(*, payload: PreviewPayload, db: Session):
    validate_override_choices(payload, db)
    return retention.preview(
        db,
        payload.mode,
        payload.value,
        overrides=[item.model_dump() for item in payload.overrides]
        if payload.overrides is not None
        else None,
    )


def preview_all(*, db: Session):
    return retention.preview(db, "all", None)


def set_policy(*, payload: SettingsPayload, db: Session, user):
    retention.lock(db)
    validate_override_choices(payload, db)
    if (
        payload.expected_updated_at is not None
        and payload.expected_updated_at != retention.policy(db)["updated_at"]
    ):
        raise DomainError(
            409,
            "Retention settings changed. Reload the page and review the current policies before saving.",
        )
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
    if payload.overrides is not None:
        db.execute(text("DELETE FROM evidence_retention_overrides"))
        for item in payload.overrides:
            db.execute(
                text(
                    "INSERT INTO evidence_retention_overrides (scope,key,mode,value) VALUES (:scope,:key,:mode,:value)"
                ),
                item.model_dump(),
            )
    # Changing a policy stops its previous snapshot; manual purge is independent.
    db.execute(
        text("""UPDATE evidence_purge_jobs SET status='cancelled',updated_at=:now
        WHERE mode <> 'all' AND status IN ('queued','running')"""),
        {"now": retention.now()},
    )
    db.commit()
    return retention.policy(db)


def purge(*, payload: PurgePayload, db: Session, user):
    retention.lock(db)
    p = retention.policy(db)
    mode, value = ("all", None) if payload.mode == "all" else (p["mode"], p["value"])
    if (
        mode == "disabled"
        and not db.execute(
            text(
                "SELECT 1 FROM evidence_retention_overrides WHERE mode <> 'disabled' LIMIT 1"
            )
        ).scalar()
    ):
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
