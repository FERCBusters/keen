"""Portable, bounded HTTP collector definitions. No executable expressions."""

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldMap(Strict):
    path: str = ""  # RFC 6901 JSON pointer, empty means whole record
    value: Any = None
    transform: Literal[
        "text", "lower", "upper", "number", "boolean", "json", "unix_ms"
    ] = "text"
    values: dict[str, str] = Field(default_factory=dict)


class Pagination(Strict):
    mode: Literal["none", "page", "offset", "cursor", "next_url", "link"] = "none"
    parameter: str = "page"
    location: Literal["query", "body"] = "query"
    start: int = Field(default=1, ge=0)
    size: int = Field(default=100, ge=1, le=1000)
    size_parameter: str = "limit"
    next_path: str = "/next"


class Enrichment(Strict):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,31}$")
    path: str

    @model_validator(mode="after")
    def check(self):
        if (
            not self.path.startswith("/")
            or self.path.startswith("//")
            or "?" in self.path
            or "#" in self.path
        ):
            raise ValueError(
                "Detail request must use a relative path without query or fragment"
            )
        return self


class Definition(Strict):
    schema_version: Literal[1] = 1
    method: Literal["GET", "POST"] = "GET"
    path: str = "/"
    query: dict[str, str | int | float | bool] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    enrichments: list[Enrichment] = Field(default_factory=list, max_length=3)
    records_path: str = ""
    fields: dict[str, FieldMap] = Field(
        default_factory=lambda: {
            "id": FieldMap(path="/id"),
            "timestamp": FieldMap(path="/created_at"),
            "summary": FieldMap(path="/title"),
        }
    )
    pagination: Pagination = Field(default_factory=Pagination)
    since_parameter: str = ""
    initial_days: int = Field(default=7, ge=1, le=365)
    overlap_seconds: int = Field(default=300, ge=0, le=86400)
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    max_pages: int = Field(default=20, ge=1, le=100)
    max_records: int = Field(default=2000, ge=1, le=10000)
    output: Literal["evidence", "measurement"] = "evidence"
    measure_id: str | None = None
    unit: str = ""

    @model_validator(mode="after")
    def check(self):
        if (
            not self.path.startswith("/")
            or self.path.startswith("//")
            or urlsplit(self.path).scheme
        ):
            raise ValueError(
                "Request path must be relative to the connection, beginning with /"
            )
        if urlsplit(self.path).query or urlsplit(self.path).fragment:
            raise ValueError("Put query parameters in Query, not in the path")
        if any(
            k.lower()
            in {
                "authorization",
                "cookie",
                "host",
                "proxy-authorization",
                "content-length",
                "transfer-encoding",
                "connection",
            }
            for k in self.headers
        ):
            raise ValueError(
                "Authentication belongs in Connections; reserved headers are not editable"
            )
        if not {"id", "timestamp", "summary"} <= self.fields.keys():
            raise ValueError("id, timestamp and summary mappings are required")
        if not self.fields.keys() <= {
            "id",
            "timestamp",
            "summary",
            "system",
            "actor",
            "action",
            "outcome",
            "severity",
            "url",
            "value",
            "period_start",
            "period_end",
        }:
            raise ValueError("Unknown evidence field")
        if self.output == "measurement" and (
            not self.measure_id
            or not {"value", "period_start", "period_end"} <= self.fields.keys()
        ):
            raise ValueError(
                "Measurements require a measure and value/period_start/period_end mappings"
            )
        for pointer in [self.records_path, self.pagination.next_path] + [
            x.path for x in self.fields.values()
        ]:
            if pointer and not pointer.startswith("/"):
                raise ValueError(
                    "Field paths use JSON pointers, for example /results or /job/status"
                )
        return self


def pointer(value, path):
    for part in path.split("/")[1:] if path else []:
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return value
