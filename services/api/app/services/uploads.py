"""Upload metadata and a bounded request-owned reader supplied by an adapter."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True)
class Upload:
    """A request-owned file. ``read`` is synchronous: operations that store
    uploads run in the thread pool, off the event loop."""

    filename: str
    content_type: str
    read: Callable[..., bytes]


def upload_size(content: BinaryIO) -> int:
    """Measure a seekable upload from its bytes, leaving it at the start."""
    content.seek(0, os.SEEK_END)
    size = content.tell()
    content.seek(0)
    return size
