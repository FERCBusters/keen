#!/usr/bin/env python3
"""Render API and UI coverage badges from the reports written by tests.sh.

Usage: coverage_badges.py RESULTS_DIR OUTPUT_DIR

RESULTS_DIR is searched recursively for backend-coverage.json (pytest-cov) and
frontend-coverage/coverage-summary.json (c8), so it works on a local
test-results/ directory or on a downloaded CI artifact.
"""

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# Approximate Verdana 11px advance widths, as used by shields.io-style badges.
_WIDTHS = {
    " ": 3.9, "%": 11.9, ".": 4.3, "0": 7.0, "1": 7.0, "2": 7.0, "3": 7.0,
    "4": 7.0, "5": 7.0, "6": 7.0, "7": 7.0, "8": 7.0, "9": 7.0, "a": 6.7,
    "b": 6.9, "c": 5.8, "d": 6.9, "e": 6.6, "f": 3.9, "g": 6.9, "h": 7.0,
    "i": 3.0, "j": 3.8, "k": 6.5, "l": 3.0, "m": 10.7, "n": 7.0, "o": 6.7,
    "p": 6.9, "q": 6.9, "r": 4.7, "s": 5.7, "t": 4.3, "u": 7.0, "v": 6.5,
    "w": 9.0, "x": 6.5, "y": 6.5, "z": 5.8, "A": 7.5, "I": 4.6, "P": 6.6,
    "U": 7.7,
}  # fmt: skip

_COLOURS = [  # (minimum percent, colour)
    (90, "#4c1"),
    (80, "#97ca00"),
    (70, "#a4a61d"),
    (60, "#dfb317"),
    (50, "#fe7d37"),
    (0, "#e05d44"),
]


def _text_width(text: str) -> float:
    return sum(_WIDTHS.get(ch, 7.0) for ch in text)


def _colour(percent: float) -> str:
    return next(colour for floor, colour in _COLOURS if percent >= floor)


def render_badge(label: str, percent: float) -> str:
    value = f"{percent:.0f}%"
    lw = round(_text_width(label) + 10)
    vw = round(_text_width(value) + 10)
    total = lw + vw
    label, value = escape(label), escape(value)
    lx, vx = lw * 5, (lw + vw / 2) * 10  # text is drawn at scale(.1)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" \
role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" \
stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/>\
<rect x="{lw}" width="{vw}" height="20" fill="{_colour(percent)}"/>\
<rect width="{total}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" \
font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110">
<text x="{lx:.0f}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)">{label}</text>
<text x="{lx:.0f}" y="140" transform="scale(.1)">{label}</text>
<text x="{vx:.0f}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)">{value}</text>
<text x="{vx:.0f}" y="140" transform="scale(.1)">{value}</text>
</g></svg>
"""


def _find_one(root: Path, pattern: str) -> Path:
    matches = sorted(root.rglob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"Expected one {pattern} under {root}, found {len(matches)}")
    return matches[0]


def _percent(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemExit(f"Coverage value is not a number: {value!r}")
    if not 0 <= value <= 100:
        raise SystemExit(f"Coverage value is out of range: {value!r}")
    return float(value)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    results, out = Path(argv[1]), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)

    api = json.loads(_find_one(results, "backend-coverage.json").read_text())
    ui = json.loads(
        _find_one(results, "frontend-coverage/coverage-summary.json").read_text()
    )
    # pytest-cov's TOTAL (statements + branches, since tests.sh uses --cov-branch).
    api_pct = _percent(api["totals"]["percent_covered"])
    # c8 line coverage, as shown in its text summary.
    ui_pct = _percent(ui["total"]["lines"]["pct"])

    (out / "coverage-api.svg").write_text(render_badge("API coverage", api_pct))
    (out / "coverage-ui.svg").write_text(render_badge("UI coverage", ui_pct))
    print(f"API coverage: {api_pct:.1f}%  UI coverage: {ui_pct:.1f}%")


if __name__ == "__main__":
    main(sys.argv)
