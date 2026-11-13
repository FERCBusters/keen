"""RSS and Atom collection against an in-memory feed server.

Feed entries become evidence events with a JSON artefact, once each across
runs. The per-feed cursor bounds what later runs reconsider, and connection
credentials reach only the connection's own origin. Events, artefacts and
cursors use the contract database and local artefact storage.
"""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import Artifact, Event, IngestionCursor
from app.ingest import rss
from app.ingest.connections import Connection, connection_scope, namespace
from app.storage.s3 import get_object_stream, parse_s3_uri

ORIGIN = "https://93.184.216.34"
FEED_URL = ORIGIN + "/security/feed.xml"

RSS2 = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<title>Supplier advisories</title><link>https://advisories.example</link>
<description>Security notices</description>
<item><guid>adv-1</guid><title>Old advisory</title>
<link>https://advisories.example/1</link>
<pubDate>Mon, 05 Jan 2026 09:00:00 GMT</pubDate>
<dc:creator>Security team</dc:creator><description>First notice</description></item>
<item><guid>adv-2</guid><title>  Patched
   release  </title><link>https://advisories.example/2</link>
<pubDate>2026-01-07T10:00:00+02:00</pubDate><author>Alerts desk</author></item>
<item><link>https://advisories.example/3</link><title></title>
<updated>2026-01-08T00:00:00</updated></item>
<item><description>An item without any identity</description></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Platform status</title>
<subtitle>Incidents</subtitle>
<link rel="self" href="https://status.example/feed.atom"/>
<link href="https://status.example/"/>
<entry><id>urn:status:1</id><title>Degraded API</title>
<link rel="related" href="https://status.example/related"/>
<link rel="alternate" href="https://status.example/incidents/1"/>
<published>2026-02-01T08:00:00Z</published><updated>2026-02-01T09:00:00Z</updated>
<author><email>ops@status.example</email></author><author><name>Operations</name></author>
<content>API latency</content></entry>
<entry><title>Maintenance file</title><link href=""/>
<link rel="enclosure" href="https://status.example/files/2"/>
<updated>2026-02-02T08:00:00Z</updated></entry>
<entry><updated>2026-02-03T08:00:00Z</updated></entry>
</feed>"""


def items(*stamps):
    body = "".join(
        f"<item><guid>entry-{i}</guid><title>Entry {i}</title>"
        f"<pubDate>{stamp}</pubDate></item>"
        for i, stamp in enumerate(stamps, 1)
    )
    return f"<rss><channel><title>Feed</title>{body}</channel></rss>".encode()


@pytest.fixture
def feeds(contract_db, monkeypatch, tmp_path):
    for name, value in {
        "demo_mode": False,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    server = SimpleNamespace(bodies={FEED_URL: RSS2}, requests=[], clients=[])

    def handler(request):
        server.requests.append(request)
        body = server.bodies.get(str(request.url))
        if body is None:
            return httpx.Response(500, text="upstream failure")
        return httpx.Response(200, content=body)

    def client(**kwargs):
        server.clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(rss, "ingestion_client", client)

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="rss")
            .order_by(Event.timestamp)
            .all()
        )

    def cursor(url=FEED_URL, label="Advisories", connection=None):
        name = rss._feed_cursor_name(url, name_hint=label)
        if connection is not None:
            # A connection's cursors live in its own namespace.
            with connection_scope(connection):
                name = namespace(name)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    return SimpleNamespace(db=contract_db, server=server, events=events, cursor=cursor)


def collect(feeds, **cfg):
    return rss.ingest_rss_feed(
        feeds.db, {"url": FEED_URL, "label": "Advisories", **cfg}
    )


def test_entries_become_evidence_once_and_move_the_cursor(feeds):
    first = collect(feeds)

    assert first == {
        "ok": True,
        "url": FEED_URL,
        "label": "Advisories",
        "system": "Advisories",
        "created_events": 3,
        "cursor_last_ts": "2026-01-08T00:00:00",
        "feed_type": "rss",
    }
    stored = feeds.events()
    # The item without a guid, link or title is skipped; the others are kept
    # oldest first with times in UTC.
    assert [(e.timestamp, e.summary, e.actor, e.action, e.system) for e in stored] == [
        (
            datetime(2026, 1, 5, 9),
            "[Advisories] Old advisory",
            "Security team",
            "published",
            "Advisories",
        ),
        (
            datetime(2026, 1, 7, 8),
            "[Advisories] Patched release",
            "Alerts desk",
            "published",
            "Advisories",
        ),
        (
            datetime(2026, 1, 8),
            "[Advisories] https://advisories.example/3",
            None,
            "updated",
            "Advisories",
        ),
    ]
    assert stored[0].external_id == rss._external_id(FEED_URL, "adv-1")
    assert stored[2].external_id == rss._external_id(
        FEED_URL, "https://advisories.example/3"
    )
    artifact = feeds.db.query(Artifact).filter_by(event_id=stored[0].id).one()
    body = get_object_stream(*parse_s3_uri(artifact.storage_uri))["Body"].read()
    snapshot = json.loads(body)
    assert snapshot["feed"] == {
        "url": FEED_URL,
        "type": "rss",
        "title": "Supplier advisories",
        "link": "https://advisories.example",
        "description": "Security notices",
    }
    assert snapshot["entry"]["summary"] == "First notice"
    assert snapshot["entry"]["timestamp"] == "2026-01-05T09:00:00"
    assert artifact.kind == "rss_entry"

    again = collect(feeds)

    assert again["created_events"] == 0
    assert len(feeds.events()) == 3
    assert feeds.cursor().last_ts == datetime(2026, 1, 8)


def test_a_later_run_reconsiders_only_the_overlap_window(feeds):
    collect(feeds)
    feeds.server.bodies[FEED_URL] = items(
        "Tue, 06 Jan 2026 12:00:00 GMT",  # older than the overlap window
        "Wed, 07 Jan 2026 23:30:00 GMT",  # inside the last hour of the cursor
        "Fri, 09 Jan 2026 08:00:00 GMT",
    )

    result = collect(feeds, overlap_minutes=60)

    assert result["created_events"] == 2
    later = [e.summary for e in feeds.events() if "Entry" in e.summary]
    assert later == ["[Advisories] Entry 2", "[Advisories] Entry 3"]
    assert feeds.cursor().last_ts == datetime(2026, 1, 9, 8)


def test_an_entry_dated_ahead_does_not_hold_back_the_entries_after_it(feeds):
    # The cursor follows the newest entry date, which is the publisher's own.
    # One entry dated ahead of the present would otherwise carry it past
    # everything the feed publishes until that date, while every run reported
    # success and stored nothing. The cursor stops at the present.
    feeds.server.bodies[FEED_URL] = items(
        "Mon, 05 Jan 2026 09:00:00 GMT", "Thu, 01 Jan 2999 00:00:00 GMT"
    )

    first = collect(feeds, overlap_minutes=60)

    assert first["created_events"] == 2
    cursor = feeds.cursor().last_ts
    assert utc_now_naive() - timedelta(minutes=1) < cursor <= utc_now_naive()
    assert first["cursor_last_ts"] == cursor.isoformat()

    # Entries published after that run are dated after the cursor and stored.
    def stamp(moment):
        return moment.strftime("%a, %d %b %Y %H:%M:%S GMT")

    feeds.server.bodies[FEED_URL] = items(
        "Mon, 05 Jan 2026 09:00:00 GMT",
        "Thu, 01 Jan 2999 00:00:00 GMT",
        stamp(utc_now_naive()),
        stamp(utc_now_naive() + timedelta(seconds=1)),
    )

    second = collect(feeds, overlap_minutes=60)

    assert second["created_events"] == 2
    assert sorted(e.summary for e in feeds.events())[-2:] == [
        "[Advisories] Entry 3",
        "[Advisories] Entry 4",
    ]
    assert feeds.cursor().last_ts <= utc_now_naive()


def test_a_cursor_is_never_set_after_the_present():
    # Every collector keeps its place in an IngestionCursor, so the bound
    # holds for all of them, in naive UTC or with a zone.
    ahead = utc_now_naive() + timedelta(days=30)
    past = datetime(2026, 1, 5, 9)
    cursor = IngestionCursor(name="bound", last_ts=past)
    assert cursor.last_ts == past

    cursor.last_ts = ahead
    assert utc_now_naive() - timedelta(minutes=1) < cursor.last_ts <= utc_now_naive()
    assert cursor.last_ts.tzinfo is None

    cursor.last_ts = ahead.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now - timedelta(minutes=1) < cursor.last_ts <= now
    cursor.last_ts = past.replace(tzinfo=timezone(timedelta(hours=11)))
    assert cursor.last_ts == past.replace(tzinfo=timezone(timedelta(hours=11)))

    cursor.last_ts = None
    assert cursor.last_ts is None


def test_a_feed_contributes_at_most_its_newest_entries(feeds):
    feeds.server.bodies[FEED_URL] = items(
        "Fri, 09 Jan 2026 08:00:00 GMT",
        "Thu, 01 Jan 2026 08:00:00 GMT",
        "Sat, 10 Jan 2026 08:00:00 GMT",
        "Fri, 02 Jan 2026 08:00:00 GMT",
    )

    result = collect(feeds, max_items=2)

    assert result["created_events"] == 2
    assert [e.summary for e in feeds.events()] == [
        "[Advisories] Entry 1",
        "[Advisories] Entry 3",
    ]


def test_atom_entries_use_their_alternate_link_author_and_content(feeds):
    feeds.server.bodies[FEED_URL] = ATOM

    result = collect(feeds, label="Status", system="status-page")

    assert (result["created_events"], result["feed_type"]) == (2, "atom")
    first, second = feeds.events()
    assert (first.summary, first.actor, first.action, first.system) == (
        "[Status] Degraded API",
        "Operations",
        "published",
        "status-page",
    )
    assert (
        first.raw_pointer["rss"]["entry_link"] == "https://status.example/incidents/1"
    )
    assert first.normalized_payload["rss"]["entry"]["summary"] == "API latency"
    assert first.normalized_payload["rss"]["feed"]["link"] == "https://status.example/"
    # Without an id, the first usable link identifies the entry.
    assert (second.actor, second.action) == (None, "updated")
    assert second.external_id == rss._external_id(
        FEED_URL, "https://status.example/files/2"
    )


def test_feed_configuration_supplies_headers_and_basic_credentials(feeds):
    collect(
        feeds,
        headers={"X-Client": "keen", "X-Blank": " ", "X-Number": 3},
        auth={"username": "reader", "password": "synthetic-password"},
    )

    (request,) = feeds.server.requests
    assert request.headers["x-client"] == "keen"
    assert "x-blank" not in request.headers and "x-number" not in request.headers
    assert request.headers["user-agent"] == settings.rss_user_agent
    assert (
        request.headers["authorization"]
        == "Basic " + base64.b64encode(b"reader:synthetic-password").decode()
    )
    # Redirects are never followed.
    assert feeds.server.clients[0]["follow_redirects"] is False


def test_connection_credentials_reach_only_the_connection_origin(feeds):
    connection = Connection(
        "db:advisories",
        "rss",
        "Advisories",
        {"base_url": ORIGIN, "username": "reader", "header_name": "X-Feed-Key"},
        {"password": "synthetic-password", "header_value": "synthetic-key"},
        None,
    )

    with connection_scope(connection):
        collect(feeds)
        elsewhere = "https://203.0.113.9/feed.xml"
        with pytest.raises(ValueError, match="connection endpoint origin"):
            rss.ingest_rss_feed(feeds.db, {"url": elsewhere, "label": "Elsewhere"})

    (request,) = feeds.server.requests
    assert request.url == FEED_URL
    # The connection sets no user agent of its own.
    assert request.headers["user-agent"] == settings.rss_user_agent
    assert request.headers["x-feed-key"] == "synthetic-key"
    assert (
        request.headers["authorization"]
        == "Basic " + base64.b64encode(b"reader:synthetic-password").decode()
    )


@pytest.mark.parametrize(
    "cfg,error",
    [
        ({"url": " "}, "missing url"),
        ({"url": "http://93.184.216.34/feed"}, "invalid or unsafe feed URL"),
        ({"url": "https://127.0.0.1/feed"}, "invalid or unsafe feed URL"),
    ],
)
def test_unusable_feed_addresses_are_refused_before_any_request(feeds, cfg, error):
    assert rss.ingest_rss_feed(feeds.db, cfg) == {"ok": False, "error": error}
    assert feeds.server.requests == [] and feeds.events() == []


def test_one_failing_feed_leaves_the_others_collected(feeds, monkeypatch):
    failing = ORIGIN + "/broken.xml"
    connection = Connection(
        "db:feeds",
        "rss",
        "Feeds",
        {},
        {},
        {
            "feeds": [
                "not a feed",
                {"url": ORIGIN + "/paused.xml", "enabled": False},
                {"url": failing, "label": "Broken"},
                {"url": FEED_URL, "label": "Advisories"},
            ]
        },
    )
    monkeypatch.setattr(settings, "rss_enabled", True)

    with patch("app.ingest.connections.connections", return_value=[connection]):
        results = rss.ingest_rss_all(feeds.db)

    assert [(r.get("url"), r.get("ok"), r.get("connection_id")) for r in results] == [
        (ORIGIN + "/paused.xml", None, "db:feeds"),
        (failing, False, "db:feeds"),
        (FEED_URL, True, "db:feeds"),
    ]
    assert results[0]["reason"] == "feed disabled"
    assert results[1]["error"] == "Collection failed: upstream returned HTTP 500"
    assert results[2]["created_events"] == 3
    # The failed feed's work is rolled back; the others are kept.
    assert feeds.cursor(failing, "Broken", connection) is None
    assert feeds.cursor(FEED_URL, "Advisories", connection).last_ts == datetime(
        2026, 1, 8
    )
    assert {e.connection_id for e in feeds.events()} == {"db:feeds"}
    assert len(feeds.events()) == 3


@pytest.mark.parametrize(
    "declaration",
    ['<!ENTITY e "' + "x" * 280 + '">', '<!ATTLIST item n CDATA "' + "y" * 1000 + '">'],
    ids=["entity", "attribute-default"],
)
def test_a_feed_declaring_an_internal_dtd_subset_stores_nothing(
    feeds, monkeypatch, declaration
):
    # Within the response limit, either declaration would expand the feed to
    # gigabytes while its tree is built.
    hostile = ORIGIN + "/hostile.xml"
    title = "&e;" if "ENTITY" in declaration else "Entry"
    feeds.server.bodies[hostile] = (
        f'<?xml version="1.0"?><!DOCTYPE rss [{declaration}]><rss><channel>'
        + "".join(
            f"<item><guid>h-{i}</guid><title>{title}</title>"
            "<pubDate>2026-01-09T00:00:00Z</pubDate></item>"
            for i in range(50)
        )
        + "</channel></rss>"
    ).encode()
    connection = Connection(
        "db:feeds",
        "rss",
        "Feeds",
        {},
        {},
        {
            "feeds": [
                {"url": hostile, "label": "Hostile"},
                {"url": FEED_URL, "label": "Advisories"},
            ]
        },
    )
    monkeypatch.setattr(settings, "rss_enabled", True)

    with patch("app.ingest.connections.connections", return_value=[connection]):
        results = rss.ingest_rss_all(feeds.db)

    assert [(r.get("url"), r.get("ok")) for r in results] == [
        (hostile, False),
        (FEED_URL, True),
    ]
    assert results[0]["error"].startswith("Collection failed;")
    assert feeds.cursor(hostile, "Hostile", connection) is None
    assert {e.system for e in feeds.events()} == {"Advisories"}


def test_collection_without_feeds_or_while_disabled_does_nothing(feeds, monkeypatch):
    empty = Connection("db:feeds", "rss", "Feeds", {}, {}, {"feeds": []})
    monkeypatch.setattr(settings, "rss_enabled", True)
    with patch("app.ingest.connections.connections", return_value=[empty]):
        assert rss.ingest_rss_all(feeds.db)[0]["note"] == "no feeds configured"

    monkeypatch.setattr(settings, "rss_enabled", False)
    assert rss.ingest_rss_all(feeds.db)[0]["skipped"] is True
    assert feeds.server.requests == []


def test_long_titles_are_shortened_in_summaries(feeds):
    title = "Advisory " + "x" * 200
    feeds.server.bodies[FEED_URL] = (
        "<rss><channel><title>Feed</title><item><guid>long</guid>"
        f"<title>{title}</title><pubDate>Fri, 09 Jan 2026 08:00:00 GMT</pubDate>"
        "</item></channel></rss>"
    ).encode()

    collect(feeds, label="")

    (event,) = feeds.events()
    assert event.summary == title[:179] + "…"
    # Without a label, the system names the feed's host and first path segment.
    assert event.system == "93.184.216.34-security"
    assert event.normalized_payload["rss"]["entry"]["title"] == title


def test_demo_collection_reads_only_the_preset_feed_within_its_limit(
    feeds, monkeypatch
):
    from app.ingest.demo_rss import FEED

    monkeypatch.setattr(settings, "demo_mode", True)
    # The preset feed's public address is resolved by DNS in a demo.
    monkeypatch.setattr(rss, "is_safe_url", lambda url: url == FEED["url"])
    feeds.server.bodies[FEED["url"]] = RSS2

    refused = rss.ingest_rss_feed(feeds.db, {"url": FEED_URL})
    assert refused == {
        "ok": False,
        "error": "Demo RSS is restricted to the preset public feed",
    }
    edited = {
        "url": FEED["url"],
        "headers": {"X-Injected": "yes"},
        "auth": {"username": "reader", "password": "synthetic-password"},
        "max_items": 1,
        "label": "Edited",
    }
    result = rss.ingest_rss_feed(feeds.db, edited)

    # Edited headers, credentials and limits are ignored in favour of the preset.
    assert (result["ok"], result["label"], result["created_events"]) == (
        True,
        FEED["label"],
        3,
    )
    (request,) = feeds.server.requests
    assert "x-injected" not in request.headers
    assert "authorization" not in request.headers

    feeds.server.bodies[FEED["url"]] = b" " * (2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="exceeds 2 MiB"):
        rss.ingest_rss_feed(feeds.db, {"url": FEED["url"]})


def test_a_demo_run_collects_the_preset_feed_whatever_the_configuration(
    feeds, monkeypatch
):
    from app.ingest.demo_rss import FEED

    for name, value in {"demo_mode": True, "rss_enabled": True}.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(rss, "is_safe_url", lambda url: url == FEED["url"])
    feeds.server.bodies[FEED["url"]] = RSS2
    configured = Connection(
        "db:feeds", "rss", "Feeds", {}, {}, {"feeds": [{"url": FEED_URL}]}
    )

    with patch("app.ingest.connections.connections", return_value=[configured]):
        (result,) = rss.ingest_rss_all(feeds.db)

    assert (result["ok"], result["created_events"]) == (True, 3)
    assert [str(r.url) for r in feeds.server.requests] == [FEED["url"]]


def test_an_entry_with_an_unreadable_date_is_kept_at_collection_time(feeds):
    feeds.server.bodies[FEED_URL] = (
        b"<rss><channel><title>Feed</title><item><guid>undated</guid>"
        b"<title>Undated</title><pubDate>not a date</pubDate></item>"
        b"</channel></rss>"
    )
    before = datetime.now(timezone.utc).replace(tzinfo=None)

    collect(feeds)

    (event,) = feeds.events()
    assert before <= event.timestamp <= datetime.now(timezone.utc).replace(tzinfo=None)
    assert event.action == "published"


def test_a_connection_can_name_its_own_user_agent(feeds):
    connection = Connection(
        "db:advisories",
        "rss",
        "Advisories",
        {"base_url": ORIGIN, "user_agent": "Supplier-Monitor/2.0"},
        {},
        None,
    )

    with connection_scope(connection):
        collect(feeds)

    assert feeds.server.requests[0].headers["user-agent"] == "Supplier-Monitor/2.0"


def test_atom_links_without_an_alternate_use_the_first_usable_one(feeds):
    feeds.server.bodies[FEED_URL] = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<title>Mirror</title>
<link rel="self" href="https://mirror.example/feed.atom"/>
<link rel="hub" href="https://hub.example/"/>
<entry><id>urn:mirror:1</id><title>Notice</title>
<link rel="related" href="https://mirror.example/related"/>
<link rel="enclosure" href="https://mirror.example/file"/>
<updated>2026-02-01T08:00:00Z</updated></entry>
</feed>"""

    collect(feeds)

    (event,) = feeds.events()
    assert event.raw_pointer["rss"]["entry_link"] == "https://mirror.example/related"
    feed = event.normalized_payload["rss"]["feed"]
    assert feed["link"] == "https://mirror.example/feed.atom"


def test_an_rss_channel_after_other_elements_is_still_read(feeds):
    feeds.server.bodies[FEED_URL] = (
        b'<rss version="2.0"><cloud domain="rpc.example" port="80"/>'
        b"<channel><title>Late channel</title>"
        b"<item><guid>late-1</guid><title>Found</title>"
        b"<pubDate>Mon, 05 Jan 2026 09:00:00 GMT</pubDate></item></channel></rss>"
    )

    result = collect(feeds)

    assert result["created_events"] == 1
    (event,) = feeds.events()
    assert event.summary == "[Advisories] Found"
    assert event.normalized_payload["rss"]["feed"]["title"] == "Late channel"


def test_a_label_without_usable_name_characters_keeps_a_digest_cursor(feeds):
    collect(feeds, label="供应商公告")

    (cursor,) = feeds.db.query(IngestionCursor).all()
    assert cursor.name == "rss:" + hashlib.sha256(FEED_URL.encode()).hexdigest()[:12]
    assert cursor.meta["label"] == "供应商公告"


def test_feed_credentials_without_a_password_send_no_authorisation(feeds):
    collect(feeds, auth={"username": "reader", "password": "  "})

    (request,) = feeds.server.requests
    assert "authorization" not in request.headers


@pytest.mark.parametrize(
    "configuration,credentials,header,authorised",
    [
        ({"header_name": "X-Feed-Key"}, {"header_value": "synthetic-key"}, True, False),
        ({"username": "reader"}, {"password": "synthetic-password"}, False, True),
    ],
    ids=["header-only", "password-only"],
)
def test_a_connection_sends_only_the_credentials_it_holds(
    feeds, configuration, credentials, header, authorised
):
    connection = Connection(
        "db:advisories",
        "rss",
        "Advisories",
        {"base_url": ORIGIN, **configuration},
        credentials,
        None,
    )

    with connection_scope(connection):
        collect(feeds)

    (request,) = feeds.server.requests
    assert ("x-feed-key" in request.headers) is header
    assert ("authorization" in request.headers) is authorised
    if header:
        assert request.headers["x-feed-key"] == "synthetic-key"
    if authorised:
        assert request.headers["authorization"] == (
            "Basic " + base64.b64encode(b"reader:synthetic-password").decode()
        )


def test_a_first_run_of_an_empty_feed_leaves_the_cursor_without_a_time(feeds):
    feeds.server.bodies[FEED_URL] = (
        b"<rss><channel><title>Quiet</title></channel></rss>"
    )

    result = collect(feeds)

    assert (result["created_events"], result["cursor_last_ts"]) == (0, None)
    assert feeds.events() == []
    # With no imported time, the next run fetches the feed in full again.
    assert feeds.cursor().last_ts is None
