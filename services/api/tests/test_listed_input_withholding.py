"""Collection inputs as administrators see them in listings, and mapping rules
whose conditions are not an object.

Credentials are withheld wherever they sit in an input, lists included, and
an RSS feed's credentials and headers are withheld whatever form they take.
Collection still reads the stored input. Requests run through the assembled
application as an administrator.
"""

import pytest
from app.db.models import SourceConnection

# Imported fixtures: administrators' evidence definition requests.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_evidence_definitions_api import FW, SECRET, defs  # noqa: F401


def _listed(editor, connection_id):
    sources = editor.admin.get("/v1/admin/source-connections")
    definitions = editor.admin.get("/v1/admin/evidence-definitions")
    for response in (sources, definitions):
        assert response.status_code == 200, response.text
        assert SECRET not in response.text
    (listed,) = [c for c in sources.json()["connections"] if c["id"] == connection_id]
    entries = [
        c["entry"]
        for c in definitions.json()["collectors"]
        if c.get("connection_id") == connection_id
    ]
    return listed["inputs"], entries


def test_credentials_inside_lists_are_withheld_from_listings(defs):  # noqa: F811
    stored = {
        "name": "deploy",
        "parameters": [
            {"name": "branch", "value": "main"},
            "plain",
            [{"api_token": SECRET}],
        ],
    }
    defs.db.add(
        SourceConnection(
            id="parameterised-ci",
            source="jenkins",
            name="Parameterised CI",
            configuration={"base_url": "https://ci.example"},
            inputs={"jobs": [stored]},
            version=1,
        )
    )
    defs.db.commit()

    inputs, entries = _listed(defs, "parameterised-ci")

    shown = {
        "name": "deploy",
        "parameters": [
            {"name": "branch", "value": "main"},
            "plain",
            [{"api_token": "[withheld]"}],
        ],
    }
    assert inputs == {"jobs": [shown]}
    assert entries == [shown]
    defs.db.expire_all()
    kept = defs.db.get(SourceConnection, "parameterised-ci").inputs["jobs"][0]
    assert kept["parameters"][2] == [{"api_token": SECRET}]


def test_feed_credentials_written_as_text_are_withheld(defs):  # noqa: F811
    defs.db.add(
        SourceConnection(
            id="text-feeds",
            source="rss",
            name="Text feeds",
            configuration={},
            inputs={
                "feeds": [
                    {
                        "url": "https://vendor.example/a.atom",
                        "auth": f"Basic {SECRET}",
                        "headers": [f"X-Tenant: {SECRET}"],
                    }
                ]
            },
            version=1,
        )
    )
    defs.db.commit()

    inputs, entries = _listed(defs, "text-feeds")

    shown = {
        "url": "https://vendor.example/a.atom",
        "auth": "[withheld]",
        "headers": "[withheld]",
    }
    assert inputs == {"feeds": [shown]}
    assert entries == [shown]


@pytest.mark.parametrize(
    "when", ["jenkins", ["jenkins"], None], ids=["text", "list", "missing"]
)
def test_a_rule_whose_conditions_are_not_an_object_is_refused(defs, when):  # noqa: F811
    rule = {"id": "deploys", "map_to": [{"framework": FW, "ref": "A.1"}]}
    if when is not None:
        rule["when"] = when

    refused = defs.admin.put(
        "/v1/admin/mapping-rules/deploys",
        json={"version": 0, "rule": rule},
        headers=unsafe(defs.admin),
    )

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "Choose a source for every rule",
    )
    assert defs.admin.get("/v1/admin/mapping-rules").json()["items"] == []
    assert defs.queued == []
