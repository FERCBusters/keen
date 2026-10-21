"""Incident creation through the assembled application and the outbound webhook.

The webhook receiver is faked at the HTTP transport, so KEEN's own code
builds, signs and sends each message.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from app.core.config import settings
from app.db.models import AuditLog, Event, EventIncident
from app.outbound import incidents

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    BASE,
    keen,  # noqa: F401
    unsafe,
)

HOOK = "https://hooks.example/incident"
SECRET = "synthetic-signing-secret"


class Receiver:
    def __init__(self):
        self.requests = []
        self.response = httpx.Response(
            202,
            headers={
                "X-Request-Id": "req-1",
                "Set-Cookie": "tracking=1",
                "Location": "https://hooks.example/incidents/1",
            },
        )

    def __call__(self, request):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def incident(keen, monkeypatch):  # noqa: F811 - fixture imported above
    for name, value in {
        "incident_webhook_url": HOOK,
        "incident_webhook_signing_secret": SECRET,
        "incident_webhook_signature_header": "X-Keen-Signature",
        "incident_webhook_headers_json": json.dumps(
            {"X-Team": "soc", "Bad Header": "dropped", "": "dropped"}
        ),
        "incident_webhook_timeout_seconds": 6.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    receiver = Receiver()
    monkeypatch.setattr(
        incidents,
        "secure_http_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(receiver), **kwargs
        ),
    )
    db = keen.db
    event = Event(
        source="ci",
        system="build",
        actor="deployer",
        action="deploy",
        outcome="failure",
        severity=7,
        summary="Deploy failed; password=hunter2 leaked in the log",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add(event)
    db.commit()
    reporter = keen.user("events.read", "incident.create")

    def create(client=None, *, title="Deploy failed", text="Rolled back", **headers):
        client = client or keen.client(reporter)
        return client.post(
            f"/v1/events/{event.id}/incident",
            json={"title": title, "text": text},
            headers={**unsafe(client), **headers},
        )

    def stored():
        db.expire_all()
        return db.query(EventIncident).filter_by(event_id=event.id).all()

    return SimpleNamespace(
        keen=keen,
        db=db,
        event=event,
        reporter=reporter,
        receiver=receiver,
        create=create,
        stored=stored,
    )


def test_an_incident_posts_a_signed_redacted_summary_linking_to_keen(incident):
    browser = incident.keen.client(incident.reporter)
    headers = unsafe(browser)
    # A client-chosen host never shapes the posted link.
    headers.pop("Origin")
    response = browser.post(
        f"/v1/events/{incident.event.id}/incident",
        json={"title": "Deploy failed", "text": "Rolled back"},
        headers={
            **headers,
            "Host": "attacker.example",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "http",
        },
    )

    assert response.status_code == 200, response.text
    link = f"{BASE}/event.html?id={incident.event.id}"
    (sent,) = incident.receiver.requests
    assert (sent.method, str(sent.url)) == ("POST", HOOK)
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["user-agent"] == "Keen Incident Webhook"
    assert sent.headers["x-team"] == "soc"
    assert "bad header" not in {k.lower() for k in sent.headers}
    expected = hmac.new(SECRET.encode(), sent.content, hashlib.sha256).hexdigest()
    assert sent.headers["x-keen-signature"] == f"sha256={expected}"

    body = json.loads(sent.content)
    assert body["type"] == "keen.incident.created"
    assert body["incident"]["title"] == "Deploy failed"
    assert body["incident"]["created_by"] == incident.reporter.username
    assert body["links"] == {"event": link}
    assert body["event"]["url"] == link
    assert body["event"]["id"] == str(incident.event.id)
    assert "hunter2" not in sent.content.decode()
    assert "attacker.example" not in sent.content.decode()

    (row,) = incident.stored()
    assert row.event_url == link and row.webhook_status_code == 202
    # Only correlation headers from the receiver are kept.
    assert row.meta == {
        "webhook_response_headers": {
            "x-request-id": "req-1",
            "location": "https://hooks.example/incidents/1",
        }
    }
    assert response.json()["incident"]["event_url"] == link
    actions = incident.db.query(AuditLog).filter_by(path="/action/incident_created")
    assert actions.count() == 1


def test_incident_text_is_stored_and_sent_as_bounded_plain_text(incident):
    response = incident.create(
        title="\x01 Deploy\x07 failed\r\n ", text="line one\r\nline two\x1b[31m\ttab"
    )

    assert response.status_code == 200, response.text
    body = json.loads(incident.receiver.requests[0].content)
    assert body["incident"]["title"] == "Deploy failed"
    assert body["incident"]["text"] == "line one\nline two[31m\ttab"
    (row,) = incident.stored()
    assert (row.title, row.text) == ("Deploy failed", "line one\nline two[31m\ttab")


@pytest.mark.parametrize(
    "title,text,status",
    [
        ("\x01\x02\x7f", "", 400),
        ("", "", 422),
        ("x" * 257, "", 422),
        ("Fine", "x" * 5001, 422),
    ],
    ids=["only-control-characters", "empty", "long-title", "long-text"],
)
def test_unusable_incident_text_sends_nothing(incident, title, text, status):
    response = incident.create(title=title, text=text)

    assert response.status_code == status
    assert incident.receiver.requests == [] and incident.stored() == []


@pytest.mark.parametrize(
    "response,status,detail",
    [
        (httpx.Response(500), 502, "Incident webhook returned HTTP 500"),
        (
            httpx.Response(302, headers={"Location": "https://attacker.example/"}),
            502,
            "Incident webhook returned HTTP 302",
        ),
        (
            httpx.ConnectError("synthetic outage"),
            502,
            "Incident webhook context failed; check connectivity and configuration",
        ),
    ],
    ids=["server-error", "redirect", "unreachable"],
)
def test_failed_deliveries_record_no_incident(incident, response, status, detail):
    incident.receiver.response = response

    refused = incident.create()

    assert refused.status_code == status
    assert refused.json()["detail"] == detail
    assert incident.stored() == []
    # A redirect is reported, never followed.
    assert [str(r.url) for r in incident.receiver.requests] == [HOOK]


@pytest.mark.parametrize(
    "url,status,detail",
    [
        ("", 503, "Incident webhook is not configured"),
        (
            "http://hooks.example/incident",
            503,
            "Incident webhook configuration is invalid",
        ),
    ],
    ids=["unconfigured", "plain-http"],
)
def test_an_unusable_webhook_destination_sends_nothing(
    incident, monkeypatch, url, status, detail
):
    monkeypatch.setattr(settings, "incident_webhook_url", url)

    refused = incident.create()

    assert refused.status_code == status
    assert refused.json()["detail"] == detail
    assert incident.receiver.requests == [] and incident.stored() == []


def test_raising_an_incident_needs_permission_and_a_visible_event(incident):
    app = incident.keen
    reader = app.client(app.user("events.read"))
    assert incident.create(reader).status_code == 403

    # A framework-link diary entry about a risk is hidden without risk access.
    hidden = Event(
        source="diary",
        system="isms",
        summary="Risk linked to controls",
        timestamp=datetime(2026, 3, 1),
        external_id=f"isms-link:risk:{uuid.uuid4()}",
        raw_pointer={},
        normalized_payload={},
    )
    incident.db.add(hidden)
    incident.db.commit()
    client = app.client(incident.reporter)
    for target, status in ((hidden.id, 404), (uuid.uuid4(), 404), ("not-a-uuid", 400)):
        response = client.post(
            f"/v1/events/{target}/incident",
            json={"title": "Look"},
            headers=unsafe(client),
        )
        assert response.status_code == status, (target, response.text)
    assert incident.receiver.requests == []


def test_incidents_are_deleted_only_with_permission_and_on_their_own_event(incident):
    created = incident.create().json()["incident"]
    app = incident.keen

    def delete(client, event_id=incident.event.id, incident_id=created["id"]):
        return client.delete(
            f"/v1/events/{event_id}/incidents/{incident_id}", headers=unsafe(client)
        )

    assert delete(app.client(incident.reporter)).status_code == 403
    remover = app.client(app.user("events.read", "incident.delete"))
    other = Event(
        source="ci",
        summary="Another event",
        timestamp=datetime(2026, 3, 2),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    incident.db.add(other)
    incident.db.commit()
    wrong_event = delete(remover, event_id=other.id)
    assert wrong_event.status_code == 404
    assert wrong_event.json()["detail"] == "Incident not found"
    assert len(incident.stored()) == 1

    assert delete(remover).json() == {"ok": True}
    assert incident.stored() == []
    deleted = incident.db.query(AuditLog).filter_by(path="/action/incident_deleted")
    assert deleted.count() == 1


def test_an_unsigned_webhook_carries_no_signature_header(incident, monkeypatch):
    monkeypatch.setattr(settings, "incident_webhook_signing_secret", "")
    response = incident.create()
    assert response.status_code == 200, response.text
    (sent,) = incident.receiver.requests
    assert "x-keen-signature" not in {k.lower() for k in sent.headers}
    assert sent.headers["x-team"] == "soc"


def test_webhook_text_and_header_configuration_are_bounded_and_tolerant():
    # Outbound text is capped after cleaning, header configuration that is
    # not a JSON object of usable names yields no extra headers, and an
    # unconfigured destination fails validation before any request.
    assert incidents.sanitize_plain_text("abcdef", max_chars=3) == "abc"
    assert incidents.sanitize_plain_text("  \x00ab\r\ncd  ", max_chars=10) == "ab\ncd"
    assert incidents._parse_json_headers(None) == {}
    assert incidents._parse_json_headers("") == {}
    assert incidents._parse_json_headers("[1, 2]") == {}
    assert incidents._parse_json_headers("not json") == {}
    assert incidents._parse_json_headers(
        json.dumps({"X-Ok": "1", "Dropped": None, " ": "x", "Tab\tName": "x"})
    ) == {"X-Ok": "1"}
    config = incidents.IncidentWebhookConfig(
        url="", headers={}, signing_secret="", signature_header="X", timeout_seconds=1
    )
    with pytest.raises(ValueError, match="not configured"):
        config.validate()


def test_action_records_bound_their_metadata_and_client_details(incident):
    # Semantic action records keep hostile or oversized request details within
    # the audit columns and tolerate metadata that cannot be serialised.
    from app.services.activity_log import record_action
    from app.services.context import ChangeContext

    class Unprintable:
        def __str__(self):
            raise RuntimeError("no text form")

    context = ChangeContext(
        method="POST",
        path="/v1/events/x/incident",
        client_ip="203.0.113.9",
        user_agent="A" * 300,
        referer="https://keen.example/" + "r" * 600,
    )
    record_action(
        incident.db,
        context,
        incident.reporter,
        "incident_tested",
        {"event_id": "e1", "skipped": None, "broken": Unprintable()},
    )
    record_action(
        incident.db, context, None, "incident_tested", {"event_id": "e1", "n": 2}
    )
    record_action(incident.db, context, None, "incident_tested")
    incident.db.commit()
    rows = (
        incident.db.query(AuditLog)
        .filter_by(path="/action/incident_tested")
        .order_by(AuditLog.id)
        .all()
    )
    assert sorted((r.username or "", r.query_string or "") for r in rows) == [
        ("", ""),
        ("", "event_id=e1&n=2"),
        (incident.reporter.username, ""),
    ]
    assert all(len(r.user_agent) == 256 and len(r.referer) == 512 for r in rows)
    assert all(r.client_ip == "203.0.113.9" and r.method == "ACTION" for r in rows)
