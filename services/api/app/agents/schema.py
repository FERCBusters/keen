import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentEvent(Strict):
    id: uuid.UUID
    timestamp: datetime
    source: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    actor: str | None = Field(default=None, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    outcome: str = Field(default="info", max_length=64)
    severity: int = Field(default=3, ge=0, le=10)
    summary: str = Field(min_length=1, max_length=4096)
    raw: str = Field(default="", max_length=65536)
    fields: dict[str, str] = Field(default_factory=dict, max_length=128)

    @field_validator("fields")
    @classmethod
    def fields_bounded(cls, value):
        if (
            any(len(k) > 256 or len(v) > 4096 for k, v in value.items())
            or len(json.dumps(value)) > 32768
        ):
            raise ValueError("Fields exceed limits")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_valid(cls, value):
        if (
            value.tzinfo is None
            or value.year < 2000
            or value > datetime.now(timezone.utc) + timedelta(minutes=5)
        ):
            raise ValueError(
                "Expected a timezone-aware event timestamp between 2000 and now + 5 minutes"
            )
        return value


class Health(Strict):
    filtered_by_source: dict[str, int] = Field(default_factory=dict, max_length=64)

    @field_validator("filtered_by_source")
    @classmethod
    def filtered_counts(cls, values):
        if any(not key or len(key) > 64 or count < 0 for key, count in values.items()):
            raise ValueError("Invalid filtered counts")
        return values

    delivered_since_start: int = Field(default=0, ge=0)
    delivery_batches_since_start: int = Field(default=0, ge=0)
    delivery_events_per_second_since_start: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    last_delivery_at: datetime | None = None
    delivery_blocked: bool = False
    updated_at: datetime | None = None
    version: str = Field(default="", max_length=64)
    queued: int = Field(default=0, ge=0)
    queue_bytes: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    sources: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("sources")
    @classmethod
    def bounded(cls, value):
        if any(len(k) > 64 or len(v) > 256 for k, v in value.items()):
            raise ValueError("Health exceeds limits")
        return value


def fingerprint(event):
    return hashlib.sha256(
        json.dumps(
            event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
