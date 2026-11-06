"""Source connection inputs are listed with their credentials withheld.

Administrators see each section of a connection's inputs: collection entries
and named webhook providers with credential values withheld, settings as
saved, and sections named for a credential withheld whole. Connections from
the deployment's connections file are listed the same way, since that file's
inputs are not checked as saved ones are. Nobody else lists connections.
"""

from app.services.managed_configurations import WITHHELD

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_source_connection_admin_api import ROOT
from tests.test_source_connection_admin_api import admin as admin

SECRET = "synthetic-secret-value"


def test_saved_inputs_are_listed_section_by_section(admin):
    logs = admin.send(
        "POST",
        ROOT,
        {
            "source": "loki",
            "name": "Production logs",
            "configuration": {"base_url": "https://logs.example"},
            "inputs": {
                "queries": [{"name": "auth", "logql": '{job="auth"}'}],
                "lookback_minutes": 15,
            },
        },
    )
    assert logs.status_code == 200, logs.text
    hook = admin.send(
        "POST",
        ROOT,
        {
            "source": "webhooks",
            "name": "Acme alerts",
            "configuration": {"provider": "acme"},
            "inputs": {"providers": {"acme": {"secret_header": "X-Acme-Signature"}}},
        },
    )
    assert hook.status_code == 200, hook.text
    shown = {c["name"]: c["inputs"] for c in admin.saved()}
    assert shown == {
        "Production logs": {
            "queries": [{"name": "auth", "logql": '{job="auth"}'}],
            "lookback_minutes": 15,
        },
        "Acme alerts": {"providers": {"acme": {"secret_header": "X-Acme-Signature"}}},
    }


def test_connections_file_credentials_are_withheld_from_everyone(
    keen, admin, monkeypatch, tmp_path
):
    connections = tmp_path / "connections.yml"
    connections.write_text(
        f"""
connections:
  - id: file-logs
    source: loki
    name: Filed logs
    configuration: {{base_url: "https://logs.example"}}
    inputs:
      queries:
        - name: auth
          logql: '{{job="auth"}}'
          headers: {{X-Api-Key: {SECRET}, Accept: text/plain}}
      lookback_minutes: 30
      api_token: {SECRET}
  - id: file-hooks
    source: webhooks
    name: Filed hooks
    configuration: {{provider: filed}}
    inputs:
      providers:
        filed: {{secret_header: X-Filed-Signature, token: {SECRET}}}
"""
    )
    monkeypatch.setenv("KEEN_SOURCE_CONNECTIONS_FILE", str(connections))

    listing = admin.get(ROOT)
    assert listing.status_code == 200, listing.text
    assert SECRET not in listing.text
    shown = {
        c["name"]: (c["managed_by"], c["inputs"]) for c in listing.json()["connections"]
    }
    assert shown["Filed logs"] == (
        "file",
        {
            "queries": [
                {
                    "name": "auth",
                    "logql": '{job="auth"}',
                    "headers": {"X-Api-Key": WITHHELD, "Accept": "text/plain"},
                }
            ],
            "lookback_minutes": 30,
            "api_token": WITHHELD,
        },
    )
    assert shown["Filed hooks"] == (
        "file",
        {
            "providers": {
                "filed": {"secret_header": "X-Filed-Signature", "token": WITHHELD}
            }
        },
    )

    for codes in ((), ("events.read", "isms.manage")):
        refused = keen.client(keen.user(*codes)).get(ROOT)
        assert refused.status_code == 403 and SECRET not in refused.text
