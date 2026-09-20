from __future__ import annotations

"""Server-side rich-text HTML sanitisation.

KEEN stores a small amount of user-authored rich text (audit report notes,
executive summaries, audit finding descriptions, ISMS meeting minutes). These
values are rendered as HTML in the browser. The UI applies a client-side
allowlist sanitiser (``sanitizeRichTextHtml`` in the MOSP design system), but
per KEEN's security model the *backend* must be the authoritative sanitiser and
the frontend is only defence-in-depth.

This module provides a single allowlist-based sanitiser that mirrors the
client-side behaviour:
  - only a fixed set of formatting tags is kept; everything else is dropped
    (its text content is preserved)
  - ``<strike>`` is normalised to ``<s>`` to match the UI
  - on ``<a>`` only a scheme-validated ``href`` is kept (http/https/mailto/tel
    or same-origin relative paths); http(s) links additionally get
    ``target="_blank" rel="noopener noreferrer"``
  - all other attributes (e.g. ``style``, ``onclick``, ``onerror``) are stripped
  - text and entities are HTML-escaped so no markup can be reconstructed

The parser is deliberately conservative and fails closed: anything it does not
explicitly recognise as safe is discarded.
"""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

# Formatting tags permitted in stored rich text. Kept in sync with the
# client-side RICH_TEXT_ALLOWED_TAGS / MINUTES_ALLOWED_HTML_TAGS allowlists.
RICH_TEXT_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "strike",
        "strong",
        "u",
        "ul",
    }
)
RICH_TEXT_VOID_TAGS: frozenset[str] = frozenset({"br", "hr"})

# Schemes permitted in <a href>. Anything else (javascript:, data:, vbscript:,
# protocol-relative //host, etc.) is dropped.
_SAFE_HREF_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "tel"})


def _safe_href(value: str | None) -> str:
    """Return a sanitised href, or '' if the URL is not safe to render."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) > 2048:
        return ""
    # Same-origin relative paths and fragment links are allowed.
    if raw.startswith("/"):
        # Reject protocol-relative URLs like //evil.com.
        if raw.startswith("//"):
            return ""
        return raw
    if raw.startswith("#"):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme.lower() in _SAFE_HREF_SCHEMES:
        return raw
    return ""


class _RichTextSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name not in RICH_TEXT_ALLOWED_TAGS:
            # Drop the tag but keep walking; child text is emitted via
            # handle_data, so content is preserved without the markup.
            return
        if name == "strike":
            name = "s"
        attr_text = ""
        if name == "a":
            attr_map = {k.lower(): v for k, v in attrs if k}
            href = _safe_href(attr_map.get("href"))
            if href:
                attr_text = f' href="{escape(href, quote=True)}"'
                if href.lower().startswith(("http://", "https://")):
                    attr_text += ' target="_blank" rel="noopener noreferrer"'
        self.parts.append(f"<{name}{attr_text}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in RICH_TEXT_VOID_TAGS:
            self.handle_starttag(name, attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name == "strike":
            name = "s"
        if name in RICH_TEXT_ALLOWED_TAGS and name not in RICH_TEXT_VOID_TAGS:
            self.parts.append(f"</{name}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        # convert_charrefs=True handles numeric/text refs inside data; this is a
        # safety net for any named entity surfaced separately.
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self.parts).strip()


def sanitize_rich_text_html(raw: str | None) -> str:
    """Return an allowlist-sanitised copy of ``raw``.

    Plain text with no angle brackets is returned unchanged (after the caller's
    own length/trim handling). Any HTML is reduced to the allowlisted subset.
    """
    value = "" if raw is None else str(raw)
    if "<" not in value and ">" not in value:
        return value
    parser = _RichTextSanitizer()
    parser.feed(value)
    parser.close()
    return parser.get_html()
