"""Upload metadata and a bounded request-owned reader supplied by an adapter."""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    read: Callable[[int], Awaitable[bytes]]


def upload_size(content: BinaryIO) -> int:
    """Measure a seekable upload from its bytes, leaving it at the start."""
    content.seek(0, os.SEEK_END)
    size = content.tell()
    content.seek(0)
    return size
