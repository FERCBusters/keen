from __future__ import annotations

import json
import re
import html as _html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import yaml
from sqlalchemy.orm import Session

from app.core.managed_configuration import load_document
from app.core.config import settings
from app.db.models import ControlItem, Event, IngestionCursor, Mapping
from app.ingest.common import is_safe_url, store_event_with_artifact


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_bookstack_ts(v: Any) -> Optional[datetime]:
    """Parse BookStack timestamps like '2020-11-28T14:43:20.000000Z' to naive UTC."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    # Python doesn't accept 'Z' in fromisoformat.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        # Assume UTC if tz is missing
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_iso_z(dt: datetime) -> str:
    """Convert a datetime to an ISO string with Z suffix, as expected by BookStack filters."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _client() -> httpx.Client:
    base = (settings.bookstack_base_url or "").rstrip("/")
    if not base:
        raise ValueError("Missing KEEN_BOOKSTACK_BASE_URL")
    # Validate the base URL to prevent SSRF attacks
    if not is_safe_url(base):
        raise ValueError("Invalid or unsafe BookStack base URL")
    tid = settings.bookstack_token_id.strip()
    tsec = settings.bookstack_token_secret.strip()
    if not tid or not tsec:
        raise ValueError("Missing BOOKSTACK_TOKEN_ID/BOOKSTACK_TOKEN_SECRET")
    headers = {
        # BookStack expects: Authorization: Token <token_id>:<token_secret>
        "Authorization": f"Token {tid}:{tsec}",
        "Accept": "application/json",
    }
    return httpx.Client(base_url=base, headers=headers, timeout=30.0, verify=True)


def load_bookstack_config(path: str) -> dict[str, Any]:
    try:
        data = load_document("bookstack", path)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _extract_book_slug(*items: dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of a BookStack book slug from list/detail payloads."""

    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("book_slug")
        if isinstance(value, str) and value.strip():
            return value.strip()
        book = item.get("book")
        if isinstance(book, dict):
            value = book.get("slug") or book.get("book_slug")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _stamp_book_scope(
    page: dict[str, Any],
    *,
    book_id: Optional[int],
    book_slug: Optional[str],
) -> dict[str, Any]:
    """Annotate API list results with the configured scope when BookStack omits it."""

    if not isinstance(page, dict):
        return page
    if book_id is not None and page.get("book_id") is None:
        page["book_id"] = int(book_id)
    if book_slug and not _extract_book_slug(page):
        page["book_slug"] = str(book_slug)
    return page


def _first_non_empty(*values: Any) -> Optional[str]:
    """Return the first non-empty string-like value."""

    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _nested_dict(root: Any, key: str) -> dict[str, Any]:
    """Return a nested dictionary if present, otherwise an empty dict."""

    if not isinstance(root, dict):
        return {}
    value = root.get(key)
    return value if isinstance(value, dict) else {}


def _event_bookstack_page_payloads(ev: Event) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build BookStack-like page payloads from an already stored event.

    Existing events may pre-date current BookStack payload fields.  Remapping
    should therefore not depend solely on fresh BookStack API calls; recover the
    page slug/book slug from normalized_payload, raw_pointer, and finally the
    system/summary strings that KEEN has historically stored.
    """

    normalized = (
        ev.normalized_payload if isinstance(ev.normalized_payload, dict) else {}
    )
    raw_pointer = ev.raw_pointer if isinstance(ev.raw_pointer, dict) else {}
    nbs = _nested_dict(normalized, "bookstack")
    rbs = _nested_dict(raw_pointer, "bookstack")

    page_id_raw = nbs.get("page_id") or rbs.get("page_id")
    if page_id_raw is None and isinstance(ev.external_id, str):
        # Current BookStack external IDs are page:<id>:<updated-at>.
        m = re.match(r"^page:(\d+):", ev.external_id.strip())
        if m:
            page_id_raw = m.group(1)
    try:
        page_id = int(page_id_raw) if page_id_raw is not None else None
    except Exception:
        page_id = None

    page_slug = _first_non_empty(
        nbs.get("page_slug"),
        rbs.get("page_slug"),
        nbs.get("slug"),
        rbs.get("slug"),
    )
    book_slug = _first_non_empty(nbs.get("book_slug"), rbs.get("book_slug"))
    title = _first_non_empty(nbs.get("page_title"), rbs.get("page_title"))

    system = str(ev.system or "").strip()
    if system:
        # Expected shape: <label>:<book-slug>:<page-slug>.  Preserve a tolerant
        # fallback for older events that only had <label>:<page-slug>.
        parts = [part.strip() for part in system.split(":") if part.strip()]
        if len(parts) >= 3:
            book_slug = book_slug or parts[1]
            page_slug = page_slug or parts[-1]
        elif len(parts) == 2:
            page_slug = page_slug or parts[1]

    summary = str(ev.summary or "").strip()
    if summary:
        if not title:
            m_title = re.search(r"page\s+'([^']+)'", summary, flags=re.IGNORECASE)
            if m_title:
                title = m_title.group(1).strip() or None
        # Summaries include a friendly combined slug suffix such as
        # "(hrnet-contract-of-employment)".  Use it as a last-resort slug
        # candidate if no direct page_slug is available.
        if not page_slug:
            m_slug = re.search(r"\(([^()]+)\)\s*$", summary)
            if m_slug:
                combined = m_slug.group(1).strip()
                if combined:
                    if book_slug and combined.lower().startswith(
                        f"{book_slug.lower()}-"
                    ):
                        page_slug = combined[len(book_slug) + 1 :]
                    else:
                        page_slug = combined

    payload: dict[str, Any] = {}
    if page_id is not None:
        payload["id"] = page_id
    if page_slug:
        payload["slug"] = page_slug
    if title:
        payload["name"] = title
    if book_slug:
        payload["book_slug"] = book_slug
        payload["book"] = {"slug": book_slug}
    return payload, dict(payload)


def _book_lookup_candidates(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        raw = data.get("data")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


_BOOK_ID_CACHE: dict[str, Optional[int]] = {}


def _resolve_book_id(client: httpx.Client, book_slug: Optional[str]) -> Optional[int]:
    """Resolve a configured BookStack book slug to its numeric ID.

    BookStack page listing/filtering is reliable with ``book_id``. Some BookStack
    versions do not include ``book_slug`` on page payloads and may not honour a
    ``filter[book_slug]`` page filter, which caused configured ``slug_regex``
    mappings scoped by book slug to silently miss pages.
    """

    slug = str(book_slug or "").strip()
    if not slug:
        return None
    cache_key = slug.lower()
    if cache_key in _BOOK_ID_CACHE:
        return _BOOK_ID_CACHE[cache_key]

    # Prefer a filtered lookup when supported, then fall back to a bounded scan.
    requests = [
        {"count": 1, "filter[slug]": slug},
        {"count": 500},
    ]
    for params in requests:
        try:
            r = client.get("/api/books", params=params)
            r.raise_for_status()
            for item in _book_lookup_candidates(r.json() or {}):
                item_slug = str(item.get("slug") or "").strip()
                if item_slug.lower() != slug.lower():
                    continue
                try:
                    book_id = int(item.get("id"))
                    _BOOK_ID_CACHE[cache_key] = book_id
                    return book_id
                except Exception:
                    _BOOK_ID_CACHE[cache_key] = None
                    return None
        except Exception:
            continue
    _BOOK_ID_CACHE[cache_key] = None
    return None


@dataclass(frozen=True)
class PageMapping:
    match_book_id: Optional[int]
    match_book_slug: Optional[str]
    match_id: Optional[int]
    match_slug: Optional[str]
    match_slug_regex: Optional[re.Pattern]
    match_title: Optional[str]
    match_title_regex: Optional[re.Pattern]
    map_to: list[str]
    confidence: float
    rationale: str

    def matches(self, page_list: dict[str, Any], page_full: dict[str, Any]) -> bool:
        pid = page_list.get("id") or page_full.get("id")
        slug = (page_list.get("slug") or page_full.get("slug") or "").strip()
        title = (page_list.get("name") or page_full.get("name") or "").strip()
        book_id = page_list.get("book_id") or page_full.get("book_id")
        book_slug = (_extract_book_slug(page_list, page_full) or "").strip()

        slug_candidates = [slug] if slug else []
        if book_slug and slug:
            combined_slug = f"{book_slug}-{slug}"
            if combined_slug not in slug_candidates:
                slug_candidates.append(combined_slug)

        if self.match_book_id is not None and int(book_id or -1) != int(
            self.match_book_id
        ):
            return False
        if self.match_book_slug and book_slug.lower() != self.match_book_slug.lower():
            return False

        if self.match_id is not None and int(pid or -1) != int(self.match_id):
            return False
        if self.match_slug and not any(
            candidate.lower() == self.match_slug.lower()
            for candidate in slug_candidates
        ):
            return False
        if self.match_slug_regex and not any(
            self.match_slug_regex.search(candidate) for candidate in slug_candidates
        ):
            return False
        if self.match_title and title.lower() != self.match_title.lower():
            return False
        if self.match_title_regex and not self.match_title_regex.search(title):
            return False
        return True


def _parse_page_mappings(cfg: dict[str, Any]) -> list[PageMapping]:
    out: list[PageMapping] = []
    raw = cfg.get("page_mappings") or cfg.get("pages") or []
    if not isinstance(raw, list):
        return out

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        match = item.get("match") if isinstance(item.get("match"), dict) else item
        map_to = item.get("map_to") or item.get("controls") or []
        if isinstance(map_to, str):
            map_to = [c.strip() for c in map_to.split(",") if c.strip()]
        if not isinstance(map_to, list) or not map_to:
            continue
        map_to = [str(c).strip() for c in map_to if str(c).strip()]

        # Optional per-mapping scope: Disambiguate page slugs across multiple books.
        mbid = match.get("book_id")
        mbslug = match.get("book_slug") or match.get("book") or ""
        # Support nested form: match: { book: { slug: ... , id: ... } }
        if isinstance(match.get("book"), dict):
            b = match.get("book")
            mbid = b.get("id") if mbid is None else mbid
            mbslug = b.get("slug") or b.get("book_slug") or mbslug
        elif isinstance(match.get("book"), str):
            mbslug = match.get("book")
        try:
            mbid = int(mbid) if mbid is not None else None
        except Exception:
            mbid = None
        mbslug = str(mbslug).strip() if mbslug is not None else None
        mbslug = mbslug or None

        mid = match.get("id")
        mslug = (match.get("slug") or match.get("page_slug") or "").strip() or None
        mslug_re_raw = (
            match.get("slug_regex") or match.get("page_slug_regex") or ""
        ).strip() or None
        mslug_re = None
        if mslug_re_raw:
            try:
                mslug_re = re.compile(mslug_re_raw)
            except re.error:
                # Skip invalid regex mappings rather than accidentally applying
                # them broadly.
                continue

        mtitle = (match.get("title") or match.get("name") or "").strip() or None
        mtitle_re_raw = (
            match.get("title_regex") or match.get("name_regex") or ""
        ).strip() or None
        mtitle_re = None
        if mtitle_re_raw:
            try:
                mtitle_re = re.compile(mtitle_re_raw)
            except re.error:
                # Skip invalid regex mappings rather than accidentally applying
                # them broadly.
                continue

        try:
            confidence = float(item.get("confidence") or 0.95)
        except Exception:
            confidence = 0.95
        rationale = str(item.get("rationale") or f"bookstack page mapping #{i+1}")

        out.append(
            PageMapping(
                match_book_id=mbid,
                match_book_slug=mbslug,
                match_id=(
                    int(mid)
                    if isinstance(mid, int) or (isinstance(mid, str) and mid.isdigit())
                    else None
                ),
                match_slug=mslug,
                match_slug_regex=mslug_re,
                match_title=mtitle,
                match_title_regex=mtitle_re,
                map_to=map_to,
                confidence=confidence,
                rationale=rationale,
            )
        )
    return out


def _get_cursor(db: Session) -> IngestionCursor:
    name = "bookstack:pages"
    cur = db.query(IngestionCursor).filter(IngestionCursor.name == name).one_or_none()
    if cur:
        return cur
    cur = IngestionCursor(name=name, last_ts=None, meta={})
    db.add(cur)
    db.flush()
    return cur


def _get_system_info(client: httpx.Client) -> dict[str, Any]:
    try:
        r = client.get("/api/system")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _list_pages(
    client: httpx.Client,
    *,
    since_ts: Optional[datetime],
    until_ts: Optional[datetime],
    book_id: Optional[int],
    book_slug: Optional[str],
    count: int,
    include_drafts: bool,
    include_templates: bool,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Return the BookStack list-page items (not full page details)."""
    count = max(1, min(int(count or 100), 500))
    out: list[dict[str, Any]] = []
    if book_id is None and book_slug:
        book_id = _resolve_book_id(client, book_slug)

    offset = 0
    while True:
        params: dict[str, Any] = {
            "count": count,
            "offset": offset,
            "sort": "-updated_at",
        }
        if since_ts:
            params["filter[updated_at:gt]"] = _to_iso_z(since_ts)
        if until_ts:
            params["filter[updated_at:lte]"] = _to_iso_z(until_ts)
        if not include_drafts:
            params["filter[draft]"] = "false"
        if not include_templates:
            params["filter[template]"] = "false"
        if book_id is not None:
            params["filter[book_id]"] = int(book_id)
        elif book_slug:
            # Compatibility fallback for BookStack versions/installations that
            # support this filter. Prefer resolved book_id whenever possible.
            params["filter[book_slug]"] = str(book_slug)

        r = client.get("/api/pages", params=params)
        r.raise_for_status()
        body = r.json() or {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data:
            break

        out.extend(
            [
                _stamp_book_scope(d, book_id=book_id, book_slug=book_slug)
                for d in data
                if isinstance(d, dict)
            ]
        )
        if len(data) < count:
            break
        offset += count
        if offset >= max_pages:
            break

    return out


def _find_page_by_slug(
    client: httpx.Client,
    slug: str,
    *,
    book_id: Optional[int],
    book_slug: Optional[str],
) -> Optional[dict[str, Any]]:
    slug = (slug or "").strip()
    if not slug:
        return None
    if book_id is None and book_slug:
        book_id = _resolve_book_id(client, book_slug)
    params: dict[str, Any] = {"count": 1, "sort": "-updated_at", "filter[slug]": slug}
    if book_id is not None:
        params["filter[book_id]"] = int(book_id)
    elif book_slug:
        params["filter[book_slug]"] = str(book_slug)
    r = client.get("/api/pages", params=params)
    r.raise_for_status()
    body = r.json() or {}
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _stamp_book_scope(data[0], book_id=book_id, book_slug=book_slug)
    return None


def _find_pages_by_slug_regex(
    client: httpx.Client,
    slug_regex: re.Pattern,
    *,
    book_id: Optional[int],
    book_slug: Optional[str],
    count: int,
    include_drafts: bool,
    include_templates: bool,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Find BookStack pages whose page slug matches a configured regex.

    BookStack's page API supports exact field filters well, but arbitrary regex
    matching is KEEN-side logic. Limit the scan to the most specific configured
    book scope available so a broad regex does not need to enumerate the whole
    instance unless the config explicitly has no book scope.
    """
    pages = _list_pages(
        client,
        since_ts=None,
        until_ts=None,
        book_id=book_id,
        book_slug=book_slug,
        count=count,
        include_drafts=include_drafts,
        include_templates=include_templates,
        max_pages=max_pages,
    )
    out: list[dict[str, Any]] = []
    for page in pages:
        slug = str(page.get("slug") or "").strip()
        if slug and slug_regex.search(slug):
            out.append(page)
    return out


def _find_page_by_title(
    client: httpx.Client,
    title: str,
    *,
    book_id: Optional[int],
    book_slug: Optional[str],
) -> Optional[dict[str, Any]]:
    title = (title or "").strip()
    if not title:
        return None
    if book_id is None and book_slug:
        book_id = _resolve_book_id(client, book_slug)
    params: dict[str, Any] = {"count": 1, "sort": "-updated_at", "filter[name]": title}
    if book_id is not None:
        params["filter[book_id]"] = int(book_id)
    elif book_slug:
        params["filter[book_slug]"] = str(book_slug)
    r = client.get("/api/pages", params=params)
    r.raise_for_status()
    body = r.json() or {}
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _stamp_book_scope(data[0], book_id=book_id, book_slug=book_slug)
    return None


def _read_page(client: httpx.Client, page_id: int) -> dict[str, Any]:
    r = client.get(f"/api/pages/{page_id}")
    r.raise_for_status()
    data = r.json() or {}
    return data if isinstance(data, dict) else {}


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_to_text(html_text: str) -> str:
    """Best-effort conversion of BookStack HTML into plain text.

    We keep this intentionally simple: remove tags, unescape entities, normalize
    whitespace.
    """
    if not html_text:
        return ""
    # Remove script/style blocks crudely.
    html_text = re.sub(
        r"(?is)<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", " ", html_text
    )
    txt = _TAG_RE.sub(" ", html_text)
    txt = _html.unescape(txt)
    # Collapse whitespace.
    return " ".join(txt.split()).strip()


def _make_preview(page_full: dict[str, Any], preview_chars: int) -> tuple[str, str]:
    """Return (preview_text, preview_source)."""
    try:
        preview_chars = int(preview_chars)
    except Exception:
        preview_chars = 500
    preview_chars = max(0, preview_chars)

    md = page_full.get("markdown")
    if isinstance(md, str) and md.strip():
        text = md.strip()
        src = "markdown"
    else:
        raw_html = page_full.get("raw_html")
        if not isinstance(raw_html, str) or not raw_html.strip():
            raw_html = page_full.get("html")
        text = _strip_html_to_text(str(raw_html or ""))
        src = "html"

    if preview_chars and len(text) > preview_chars:
        text = text[:preview_chars]
    return text, src


def _page_url(
    base_url: str, book_slug: Optional[str], page_slug: Optional[str]
) -> Optional[str]:
    base = (base_url or "").rstrip("/")
    b = (book_slug or "").strip()
    p = (page_slug or "").strip()
    if not base or not b or not p:
        return None
    # This form works for pages even if nested in a chapter (BookStack will route accordingly).
    return f"{base}/books/{b}/page/{p}"


def _page_direct_url(base_url: str, page_id: int) -> Optional[str]:
    """Return the stable id-based page URL.

    BookStack provides direct links in the format <base_url>/link/<page_id>.
    """
    base = (base_url or "").rstrip("/")
    try:
        pid = int(page_id)
    except Exception:
        return None
    if not base or pid <= 0:
        return None
    return f"{base}/link/{pid}"


def _ensure_and_add_mappings(
    db: Session,
    *,
    event_id: str,
    refs: list[str],
    confidence: float,
    method: str,
    rationale: str,
) -> int:
    # Normalize refs
    refs = sorted({r.strip() for r in refs if isinstance(r, str) and r.strip()})
    if not refs:
        return 0

    # Ensure control items exist
    found = (
        db.query(ControlItem)
        .filter(
            ControlItem.framework_slug == settings.default_framework_slug,
            ControlItem.ref.in_(refs),
        )
        .all()
    )
    by_ref = {ci.ref: ci for ci in found}
    for ref in refs:
        if ref in by_ref:
            continue
        ci_type = "annex_control" if ref.upper().startswith("A.") else "clause"
        ci = ControlItem(
            framework_slug=settings.default_framework_slug,
            type=ci_type,
            ref=ref,
            title=None,
        )
        db.add(ci)
        db.flush()
        by_ref[ref] = ci

    created = 0
    for ref in refs:
        ci = by_ref.get(ref)
        if not ci:
            continue
        exists = (
            db.query(Mapping.id)
            .filter(Mapping.event_id == event_id, Mapping.control_item_id == ci.id)
            .scalar()
        )
        if exists:
            continue
        mp = Mapping(
            event_id=event_id,
            control_item_id=ci.id,
            confidence=confidence,
            method=method,
            rationale=rationale,
            mapped_by="system",
        )
        db.add(mp)
        created += 1
    if created:
        db.commit()
    return created


def apply_bookstack_config_mappings(db: Session, ev: Event) -> int:
    """Apply config/bookstack.yml page mappings to an existing BookStack event.

    The generic remap flow applies mapping-rules.yml only.  BookStack page
    mappings live in config/bookstack.yml, so historical/deduped BookStack
    events need this source-specific pass when an admin runs remapping after
    editing BookStack page mapping rules.
    """

    if (ev.source or "").strip().lower() != "bookstack":
        return 0

    cfg = load_bookstack_config(settings.bookstack_config_path)
    page_mappings = _parse_page_mappings(cfg)
    if not page_mappings:
        return 0

    page_list, page_full = _event_bookstack_page_payloads(ev)
    if not page_list and not page_full:
        return 0

    created = 0
    for pm in page_mappings:
        if not pm.matches(page_list, page_full):
            continue
        created += _ensure_and_add_mappings(
            db,
            event_id=str(ev.id),
            refs=pm.map_to,
            confidence=pm.confidence,
            method="config",
            rationale=f"bookstack config mapping: {pm.rationale}",
        )
    return created


def ingest_bookstack_all(db: Session) -> list[dict[str, Any]]:
    """Poll BookStack for page changes and store each page snapshot as an evidence event."""
    if not settings.bookstack_enabled:
        return [{"ok": True, "skipped": True, "reason": "bookstack disabled"}]

    cfg = load_bookstack_config(settings.bookstack_config_path)
    label = str(cfg.get("label") or "bookstack").strip() or "bookstack"
    poll = cfg.get("poll") if isinstance(cfg.get("poll"), dict) else {}
    lookback_days = int(poll.get("lookback_days") or 30)
    count = int(poll.get("count") or 200)
    include_drafts = bool(poll.get("include_drafts") or False)
    include_templates = bool(poll.get("include_templates") or False)
    max_pages = int(poll.get("max_pages") or 5000)

    # Optional scope: Restrict ingest to a single ISMS book.
    # (Your ISMS in BookStack is typically organized as a book.)
    book_cfg = cfg.get("book") if isinstance(cfg.get("book"), dict) else {}
    book_id = book_cfg.get("id") if book_cfg else cfg.get("book_id")
    book_slug = book_cfg.get("slug") if book_cfg else cfg.get("book_slug")
    try:
        book_id = int(book_id) if book_id is not None else None
    except Exception:
        book_id = None
    book_slug = str(book_slug).strip() if book_slug is not None else None

    # Optional scope: Restrict ingest to multiple books.
    # Example:
    #   books:
    #     - slug: "isms"
    #     - slug: "risk-register"
    # If `book:` is set, it takes precedence over `books:`.
    books_raw = cfg.get("books") if isinstance(cfg.get("books"), list) else []
    books: list[tuple[Optional[int], Optional[str]]] = []
    for b in books_raw:
        if isinstance(b, str):
            slug = b.strip()
            if slug:
                books.append((None, slug))
            continue
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        bslug = b.get("slug") or b.get("book_slug")
        try:
            bid = int(bid) if bid is not None else None
        except Exception:
            bid = None
        bslug = str(bslug).strip() if bslug is not None else None
        if bid is None and not bslug:
            continue
        books.append((bid, bslug or None))

    # Evidence capture settings
    capture = cfg.get("capture") if isinstance(cfg.get("capture"), dict) else {}
    try:
        preview_chars = int(
            capture.get("preview_chars") or cfg.get("preview_chars") or 500
        )
    except Exception:
        preview_chars = 500
    preview_chars = max(0, preview_chars)
    capture_mode = str(capture.get("mode") or "preview").strip().lower()
    capture_full_content = bool(
        capture.get("full_content") or False
    ) or capture_mode in ("full", "all")

    page_mappings = _parse_page_mappings(cfg)
    capture_pages = _parse_page_mappings({"page_mappings": [
        {"match": selector, "map_to": ["__capture_only__"]}
        for selector in (cfg.get("capture_pages") or [])
        if isinstance(selector, dict)
    ]})
    seed_mapped_pages = bool(
        cfg.get("seed_mapped_pages") if "seed_mapped_pages" in cfg else True
    )

    cur = _get_cursor(db)
    run_started = _utcnow().replace(microsecond=0)
    since_ts = cur.last_ts
    if since_ts is None:
        since_ts = (run_started - timedelta(days=max(1, lookback_days))).replace(
            tzinfo=None
        )

    runs: list[dict[str, Any]] = []
    with _client() as client:
        sysinfo = _get_system_info(client)
        instance_name = (
            (sysinfo.get("app_name") or label) if isinstance(sysinfo, dict) else label
        )
        instance_base_url = (
            (sysinfo.get("base_url") or settings.bookstack_base_url)
            if isinstance(sysinfo, dict)
            else settings.bookstack_base_url
        )

        # 1) Gather updated pages in a bounded window (since cursor, up to run start).
        updated_pages: list[dict[str, Any]] = []
        if book_id is not None or book_slug:
            updated_pages = _list_pages(
                client,
                since_ts=since_ts,
                until_ts=run_started,
                book_id=book_id,
                book_slug=book_slug,
                count=count,
                include_drafts=include_drafts,
                include_templates=include_templates,
                max_pages=max_pages,
            )
        elif books:
            # Merge results across multiple configured books.
            for bid, bslug in books:
                try:
                    updated_pages.extend(
                        _list_pages(
                            client,
                            since_ts=since_ts,
                            until_ts=run_started,
                            book_id=bid,
                            book_slug=bslug,
                            count=count,
                            include_drafts=include_drafts,
                            include_templates=include_templates,
                            max_pages=max_pages,
                        )
                    )
                except Exception:
                    continue
        else:
            # No book scoping: list across the whole instance.
            updated_pages = _list_pages(
                client,
                since_ts=since_ts,
                until_ts=run_started,
                book_id=None,
                book_slug=None,
                count=count,
                include_drafts=include_drafts,
                include_templates=include_templates,
                max_pages=max_pages,
            )

        pages_by_id: dict[int, dict[str, Any]] = {}
        for p in updated_pages:
            pid = p.get("id")
            if isinstance(pid, int):
                pages_by_id[pid] = p

        # Pages selected through the evidence definition editor must be
        # captured even when they have not changed inside the poll window.
        for selected in cfg.get("selected_pages", []) or []:
            pid = selected.get("id") if isinstance(selected, dict) else None
            if isinstance(pid, int) and pid > 0:
                pages_by_id.setdefault(pid, {
                    "id": pid, "book_id": selected.get("book_id"),
                    "book_slug": selected.get("book_slug"),
                })

        # 2) Ensure configured pages are captured at least once (as snapshots).
        if seed_mapped_pages and (page_mappings or capture_pages):
            for pm in [*page_mappings, *capture_pages]:
                try:
                    if pm.match_id is not None:
                        pages_by_id.setdefault(
                            int(pm.match_id),
                            {
                                "id": int(pm.match_id),
                                "book_id": pm.match_book_id,
                                "book_slug": pm.match_book_slug,
                            },
                        )
                        continue
                    if pm.match_slug:
                        # Resolve within the most specific scope available: mapping -> top-level book -> books list -> global.
                        scopes: list[tuple[Optional[int], Optional[str]]] = []
                        if pm.match_book_id is not None or pm.match_book_slug:
                            scopes = [(pm.match_book_id, pm.match_book_slug)]
                        elif book_id is not None or book_slug:
                            scopes = [(book_id, book_slug)]
                        elif books:
                            scopes = books
                        else:
                            scopes = [(None, None)]

                        p = None
                        for bid, bslug in scopes:
                            try:
                                p = _find_page_by_slug(
                                    client, pm.match_slug, book_id=bid, book_slug=bslug
                                )
                            except Exception:
                                p = None
                            if p:
                                break
                        if p and isinstance(p.get("id"), int):
                            pages_by_id.setdefault(int(p["id"]), p)
                        continue
                    if pm.match_slug_regex:
                        # Regex page-slug mappings can match multiple pages. There is no
                        # generic BookStack regex filter, so list within the narrowest
                        # available book scope and apply the regex in KEEN.
                        scopes: list[tuple[Optional[int], Optional[str]]] = []
                        if pm.match_book_id is not None or pm.match_book_slug:
                            scopes = [(pm.match_book_id, pm.match_book_slug)]
                        elif book_id is not None or book_slug:
                            scopes = [(book_id, book_slug)]
                        elif books:
                            scopes = books
                        else:
                            scopes = [(None, None)]

                        for bid, bslug in scopes:
                            try:
                                for p in _find_pages_by_slug_regex(
                                    client,
                                    pm.match_slug_regex,
                                    book_id=bid,
                                    book_slug=bslug,
                                    count=count,
                                    include_drafts=include_drafts,
                                    include_templates=include_templates,
                                    max_pages=max_pages,
                                ):
                                    if isinstance(p.get("id"), int):
                                        pages_by_id.setdefault(
                                            int(p["id"]),
                                            _stamp_book_scope(
                                                p, book_id=bid, book_slug=bslug
                                            ),
                                        )
                            except Exception:
                                continue
                        continue
                    if pm.match_title:
                        scopes: list[tuple[Optional[int], Optional[str]]] = []
                        if pm.match_book_id is not None or pm.match_book_slug:
                            scopes = [(pm.match_book_id, pm.match_book_slug)]
                        elif book_id is not None or book_slug:
                            scopes = [(book_id, book_slug)]
                        elif books:
                            scopes = books
                        else:
                            scopes = [(None, None)]

                        p = None
                        for bid, bslug in scopes:
                            try:
                                p = _find_page_by_title(
                                    client, pm.match_title, book_id=bid, book_slug=bslug
                                )
                            except Exception:
                                p = None
                            if p:
                                break
                        if p and isinstance(p.get("id"), int):
                            pages_by_id.setdefault(int(p["id"]), p)
                except Exception:
                    # Keep ingestion robust even if a mapping entry is wrong.
                    continue

        created = 0
        deduped = 0
        errors: list[str] = []
        config_mappings_created = 0

        for pid, page_list in sorted(pages_by_id.items(), key=lambda kv: int(kv[0])):
            try:
                page_list_book_id = (
                    page_list.get("book_id") if isinstance(page_list, dict) else None
                )
                page_full = _stamp_book_scope(
                    _read_page(client, pid),
                    book_id=page_list_book_id,
                    book_slug=_extract_book_slug(page_list),
                )
                page_slug = (
                    page_full.get("slug") or page_list.get("slug") or ""
                ).strip() or None
                page_title = (
                    page_full.get("name") or page_list.get("name") or ""
                ).strip() or f"Page {pid}"
                page_book_slug = _extract_book_slug(page_list, page_full)

                created_at = _parse_bookstack_ts(
                    page_full.get("created_at") or page_list.get("created_at")
                )
                updated_at = _parse_bookstack_ts(
                    page_full.get("updated_at") or page_list.get("updated_at")
                )
                ts = updated_at or created_at or run_started.replace(tzinfo=None)

                is_updated_in_window = bool(
                    updated_at and updated_at > (cur.last_ts or datetime.min)
                )
                is_created = bool(
                    created_at
                    and updated_at
                    and created_at == updated_at
                    and is_updated_in_window
                )
                action = (
                    "created"
                    if is_created
                    else ("updated" if is_updated_in_window else "snapshot")
                )

                actor = None
                ub = page_full.get("updated_by")
                if isinstance(ub, dict):
                    actor = (ub.get("name") or ub.get("slug") or "").strip() or None
                if not actor and isinstance(page_full.get("created_by"), dict):
                    cb = page_full.get("created_by")
                    actor = (cb.get("name") or cb.get("slug") or "").strip() or None

                # Prefer the configured book scope if present, otherwise fall back
                # to the list response's book_slug.
                book_slug_eff = book_slug or page_book_slug
                url_slug = _page_url(instance_base_url, book_slug_eff, page_slug)
                url_direct = _page_direct_url(instance_base_url, pid)
                primary_url = url_direct or url_slug

                preview_text, preview_src = _make_preview(page_full, preview_chars)

                # Store a small snapshot for evidence. Default is a preview-only
                # capture (no full page content) with a link.
                page_snapshot: dict[str, Any] = {
                    "id": pid,
                    "book_id": page_full.get("book_id") or page_list.get("book_id"),
                    "chapter_id": page_full.get("chapter_id")
                    or page_list.get("chapter_id"),
                    "name": page_title,
                    "slug": page_slug,
                    "book_slug": book_slug_eff,
                    "draft": (
                        page_full.get("draft")
                        if "draft" in page_full
                        else page_list.get("draft")
                    ),
                    "template": (
                        page_full.get("template")
                        if "template" in page_full
                        else page_list.get("template")
                    ),
                    "revision_count": (
                        page_full.get("revision_count")
                        if "revision_count" in page_full
                        else page_list.get("revision_count")
                    ),
                    "editor": (
                        page_full.get("editor")
                        if "editor" in page_full
                        else page_list.get("editor")
                    ),
                    "created_at": str(
                        page_full.get("created_at") or page_list.get("created_at") or ""
                    ),
                    "updated_at": str(
                        page_full.get("updated_at") or page_list.get("updated_at") or ""
                    ),
                    "updated_by": actor,
                    "url": primary_url,
                    "url_slug": url_slug,
                    "url_direct": url_direct,
                    "preview": preview_text,
                    "preview_source": preview_src,
                    "preview_chars": preview_chars,
                }
                tags = page_full.get("tags")
                if isinstance(tags, list):
                    page_snapshot["tags"] = tags
                if capture_full_content:
                    # Optional, off by default.
                    page_snapshot["html"] = page_full.get("html")
                    page_snapshot["raw_html"] = page_full.get("raw_html")
                    page_snapshot["markdown"] = page_full.get("markdown")

                payload = {
                    "instance": {
                        "app_name": instance_name,
                        "base_url": (instance_base_url or "").rstrip("/"),
                    },
                    "page": page_snapshot,
                }
                payload_bytes = json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
                updated_at_raw = str(
                    page_full.get("updated_at") or page_list.get("updated_at") or ""
                ).strip()
                ext_id = f"page:{pid}:{updated_at_raw or 'unknown'}"

                day = ts.date().isoformat()
                safe_page = (page_slug or f"page-{pid}").replace("/", "-")
                safe_book = (
                    book_slug_eff or (f"book-{book_id}" if book_id else "")
                ).replace("/", "-")
                safe_slug = "-".join([x for x in (safe_book, safe_page) if x])
                key = f"bookstack/{label}/{day}/{safe_slug}-{pid}.json"

                system_parts = [label]
                if book_slug_eff:
                    system_parts.append(book_slug_eff)
                if safe_page:
                    system_parts.append(safe_page)
                system = ":".join(system_parts)
                summary = f"bookstack: {action} page '{page_title}'" + (
                    f" ({safe_slug})" if safe_slug else ""
                )

                res = store_event_with_artifact(
                    db,
                    # Keep timestamps UTC-naive for consistency with other ingesters.
                    timestamp=ts,
                    source="bookstack",
                    system=system,
                    actor=actor,
                    action=action,
                    outcome="info",
                    severity=2,
                    summary=summary,
                    raw_pointer={
                        "bookstack": {
                            "instance": (instance_base_url or "").rstrip("/"),
                            "page_id": pid,
                            "book_id": page_full.get("book_id") or page_list.get("book_id") or book_id,
                            "page_slug": page_slug,
                            "book_slug": book_slug_eff,
                            "url": primary_url,
                            "url_slug": url_slug,
                            "url_direct": url_direct,
                        }
                    },
                    normalized_payload={
                        "bookstack": {
                            "instance": instance_name,
                            "page_id": pid,
                            "page_slug": page_slug,
                            "page_title": page_title,
                            "book_slug": book_slug_eff,
                            "action": action,
                            "url": primary_url,
                            "created_at": str(
                                page_full.get("created_at")
                                or page_list.get("created_at")
                                or ""
                            ),
                            "updated_at": str(
                                page_full.get("updated_at")
                                or page_list.get("updated_at")
                                or ""
                            ),
                            "preview": preview_text,
                            "preview_source": preview_src,
                            "preview_chars": preview_chars,
                        }
                    },
                    external_id=ext_id,
                    artifact_kind="bookstack_page",
                    artifact_bytes=payload_bytes,
                    artifact_content_type="application/json",
                    artifact_key=key,
                    captured_by="keen:bookstack",
                )

                if res.get("deduped"):
                    deduped += 1
                else:
                    created += 1

                # Apply configured page->control mappings (method=config) for this event.
                eid = str(res.get("event_id") or "").strip()
                if eid and page_mappings:
                    for pm in page_mappings:
                        if not pm.matches(page_list, page_full):
                            continue
                        rationale = f"bookstack config mapping: {pm.rationale}"
                        config_mappings_created += _ensure_and_add_mappings(
                            db,
                            event_id=eid,
                            refs=pm.map_to,
                            confidence=pm.confidence,
                            method="config",
                            rationale=rationale,
                        )

                runs.append(res)
            except Exception as e:
                errors.append(f"page_id={pid}: {type(e).__name__}: {e}")

        # Cursor: Move the window forward to the start of this run.
        cur.last_ts = run_started.replace(tzinfo=None)
        db.commit()

        runs.insert(
            0,
            {
                "ok": True,
                "source": "bookstack",
                "instance": instance_name,
                "created": created,
                "deduped": deduped,
                "config_mappings_created": config_mappings_created,
                "errors": errors,
                "cursor_set_to": cur.last_ts.isoformat() if cur.last_ts else None,
            },
        )

    return runs
