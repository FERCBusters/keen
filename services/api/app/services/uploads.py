"""Upload metadata and a bounded request-owned reader supplied by an adapter."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    read: Callable[[], Awaitable[bytes]]
