"""KEEN's own modules and the myf modules it runs on, scanned as one tree.

Source contracts hold for every module KEEN runs, wherever it lives. A path
is named relative to its own package root, so ``security/regex.py`` names the
module whether it sits in ``app`` or in ``myf``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import myf

API = Path(__file__).resolve().parents[1]
ROOTS = (API / "app", Path(myf.__file__).resolve().parent)


class SourceTree:
    def __init__(self, *parts: str) -> None:
        self.parts = parts

    def __truediv__(self, part: str) -> SourceTree | Path:
        parts = (*self.parts, part)
        for root in ROOTS:
            candidate = root.joinpath(*parts)
            if candidate.is_file():
                return candidate
        return SourceTree(*parts)

    def rglob(self, pattern: str) -> Iterator[Path]:
        for root in ROOTS:
            base = root.joinpath(*self.parts)
            if base.is_dir():
                yield from base.rglob(pattern)

    def is_dir(self) -> bool:
        return any(root.joinpath(*self.parts).is_dir() for root in ROOTS)


SOURCES = SourceTree()


def relative(path: Path) -> PurePosixPath:
    """``path`` named from its package root (``app`` or ``myf``)."""
    for root in ROOTS:
        if path.is_relative_to(root):
            return PurePosixPath(path.relative_to(root).as_posix())
    raise ValueError(f"{path} is outside KEEN's sources")


def name_in(path: Path, root: SourceTree | Path) -> PurePosixPath:
    """``path`` named from ``root``: a package root for the source tree."""
    if isinstance(root, SourceTree):
        return relative(path)
    return PurePosixPath(path.relative_to(root).as_posix())
