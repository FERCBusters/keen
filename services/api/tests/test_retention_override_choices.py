"""Retention override choices and saved overrides, through the assembled app.

Administrators may override retention for each exact source type: webhook
providers of saved webhook connections and custom integration collectors are
offered by their source IDs, and saved overrides are stored exactly.
"""

import uuid

from app.db.models import IntegrationCollector, IntegrationConnection, SourceConnection

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_evidence_retention_admin_api import lab as lab


def test_saved_webhook_connections_and_collectors_are_offered_by_source_id(lab):
    db = lab.db
    db.add_all(
        [
            SourceConnection(
                id="webhook-acme",
                source="webhooks",
                name="Acme alerts",
                configuration={"provider": "acme"},
            ),
            SourceConnection(
                id="webhook-unnamed",
                source="webhooks",
                name="Unfinished",
                configuration={},
            ),
            SourceConnection(
                id="loki-prod",
                source="loki",
                name="Production logs",
                configuration={"provider": "not-a-webhook"},
            ),
        ]
    )
    upstream = IntegrationConnection(
        id=str(uuid.uuid4()), name="Ticketing", base_url="https://tickets.example"
    )
    db.add(upstream)
    db.flush()
    collector = IntegrationCollector(
        id=str(uuid.uuid4()),
        name="Ticket changes",
        connection_id=upstream.id,
        draft={},
    )
    db.add(collector)
    db.commit()

    sources = {
        item["key"]: item["label"] for item in lab.call("GET")["choices"]["source"]
    }
    assert sources["webhook:acme"] == "Webhook: acme"
    assert sources["webhook:synthetic"] == "Webhook: synthetic"
    assert sources["integration:" + collector.id] == "Ticket changes"
    webhooks = sorted(key for key in sources if key.startswith("webhook:"))
    assert webhooks == ["webhook:acme", "webhook:synthetic"]

    # An offered source ID can be given its own retention.
    saved = lab.call(
        "PUT",
        "",
        {
            "mode": "disabled",
            "overrides": [
                {"scope": "source", "key": "webhook:acme", "mode": "age", "value": 30},
                {
                    "scope": "source",
                    "key": "integration:" + collector.id,
                    "mode": "count",
                    "value": 100,
                },
            ],
        },
    )
    assert (saved["mode"], saved["updated_by"]) == ("disabled", lab.username)
    assert lab.sql(
        "SELECT scope, key, mode, value FROM evidence_retention_overrides ORDER BY key"
    ) == [
        {
            "scope": "source",
            "key": "integration:" + collector.id,
            "mode": "count",
            "value": 100,
        },
        {"scope": "source", "key": "webhook:acme", "mode": "age", "value": 30},
    ]
    # Replacing the overrides removes those no longer listed.
    lab.call(
        "PUT",
        "",
        {
            "mode": "disabled",
            "overrides": [
                {"scope": "source", "key": "webhook:acme", "mode": "disabled"}
            ],
        },
    )
    assert lab.sql(
        "SELECT scope, key, mode, value FROM evidence_retention_overrides"
    ) == [{"scope": "source", "key": "webhook:acme", "mode": "disabled", "value": None}]
