"""RSS and Atom collection against an in-memory feed server.

Feed entries become evidence events with a JSON artefact, once each across
runs. The per-feed cursor bounds what later runs reconsider, and connection
credentials reach only the connection's own origin. Events, artefacts and
cursors use the contract database and local artefact storage.
"""

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from app.core.config import settings
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
