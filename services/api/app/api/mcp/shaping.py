"""Keen's API answers, shaped for language models: compact, bounded, labelled.

Pages need fields a model does not: duplicates of other fields, presentation
values and long markup. A model reads every byte it is given, so each answer
keeps the fields that carry meaning, shortens long text and caps its size.
Text Keen collected from other systems is set apart under ``untrusted``.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any

# Page-only fields, and fields that repeat another.
NOISE = frozenset(
    {
        "display",
        "href",
        "asset_entity",
        "mitigator_context",
        "content_html",
        "resource_user_ids",
        "attendee_user_ids",
        "apology_user_ids",
        "attendee_person_ids",
        "apology_person_ids",
        "license_entity",
        "category_id",
        "subcategory_id",
        "name_id",
        "nature_id",
        "license_id",
        "owner_org_node_id",
        "justification_options",
        "can_view_risks",
        "can_view_pestle",
        "can_view_interested_parties",
        "can_view_isms",
        "can_view_swot",
    }
)
TEXT_LIMIT = 2000
LIST_LIMIT = 50
DEPTH_LIMIT = 4
RESULT_BYTES = 60_000
DOCUMENT_TEXT_LIMIT = 40_000
TRUNCATED = " [truncated]"
_EMPTY: tuple[object, ...] = (None, "", [], {})


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATED


def compact(value: Any, depth: int = 0) -> Any:
    """``value`` without page-only and empty fields, with lists and text capped."""
    if depth >= DEPTH_LIMIT and isinstance(value, (dict, list)):
        return TRUNCATED.strip()
    if isinstance(value, dict):
        return {
            key: compact(item, depth + 1)
            for key, item in value.items()
            if key not in NOISE and item not in _EMPTY
        }
    if isinstance(value, list):
        return [compact(item, depth + 1) for item in value[:LIST_LIMIT]]
    if isinstance(value, str):
        return clip(value)
    return value


def fit(result: dict) -> dict:
    """Shorten ``result``'s longest lists until the answer fits its budget.

    Lists at the top level and one level down are considered, so a record and
    its linked records both stay within RESULT_BYTES.
    """
    while len(json.dumps(result, default=str)) > RESULT_BYTES:
        lists = [value for value in result.values() if isinstance(value, list)]
        lists += [
            inner
            for value in result.values()
            if isinstance(value, dict)
            for inner in value.values()
            if isinstance(inner, list)
        ]
        lists = [value for value in lists if value]
        if not lists:
            break
        longest = max(lists, key=len)
        del longest[-max(1, len(longest) // 4) :]
        result["truncated"] = True
    return result


def listing(answer: dict, project=compact) -> dict:
    """A paged list answer: its total, the projected items and the next offset."""
    items = [project(item) for item in answer.get("items", [])]
    offset = int(answer.get("offset") or 0)
    total = int(answer.get("total") or len(items))
    out = {"total": total, "offset": offset, "items": items}
    if offset + len(items) < total:
        out["next_offset"] = offset + len(items)
    return fit(out)


_EVIDENCE_FIELDS = (
    "id",
    "timestamp",
    "source",
    "connection_name",
    "system",
    "action",
    "outcome",
    "severity",
    "artifact_count",
    "controls",
)
# Collected from the systems Keen watches; anyone able to write to those
# systems can write this text.
_UNTRUSTED_FIELDS = ("summary", "actor", "identifier", "source_url")


def evidence(item: dict) -> dict:
    """An evidence event, with the text other systems supplied set apart."""
    out = compact({key: item.get(key) for key in _EVIDENCE_FIELDS})
    untrusted = compact({key: item.get(key) for key in _UNTRUSTED_FIELDS})
    if untrusted:
        out["untrusted"] = untrusted
    return out


_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset(
    {"p", "div", "br", "tr", "blockquote", "pre", "table", "ul", "ol", "hr"}
)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _HEADINGS:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADINGS or tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: str, limit: int = DOCUMENT_TEXT_LIMIT) -> str:
    """Readable text from Keen's sanitised rich text, headings marked with #."""
    parser = _Text()
    # Markup takes more characters than the text it holds; a fixed multiple of
    # the limit is read, so a long document costs no more than its excerpt.
    parser.feed(html[: limit * 4])
    parser.close()
    lines: list[str] = []
    for line in "".join(parser.parts).splitlines():
        line = " ".join(line.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    return clip("\n".join(lines).strip(), limit)
