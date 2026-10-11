"""File export results consumed by HTTP and other entry points."""

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class Export:
    content: Iterable[str | bytes]
    content_type: str
    filename: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
