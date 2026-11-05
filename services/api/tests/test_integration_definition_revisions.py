"""Custom API collector definitions: lists in request bodies and republishing.

Every element of a list in a definition is checked for credential names, and
a list without any is saved as written. Publishing a version that is already
a revision keeps that one revision. Requests run through the assembled
application as an administrator.
"""

from app.db.models import IntegrationRevision

# Imported fixture: administrators' integration requests through the assembled app.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_integration_admin_api import DEFINITION, api, collector  # noqa: F401

LISTED = {
    **DEFINITION,
    "method": "POST",
    "body": {"filters": [{"field": "status"}, ["nested", 1], "plain", 2, None]},
}


def test_lists_without_credential_names_are_saved_as_written(api):  # noqa: F811
    saved = collector(api, LISTED)

    assert saved["draft"]["body"] == LISTED["body"]
    # Every element is checked, including those after clean ones.
    late = {
        **LISTED,
        "body": {"filters": [{"field": "a"}, {"field": "b", "token": "x"}]},
    }
    refused = collector(api, late, status=422)
    assert refused["detail"] == "Move credentials from the definition to a Connection"


def test_publishing_a_published_version_again_keeps_one_revision(api):  # noqa: F811
    key = collector(api)["id"]
    first = api.call("POST", f"/collectors/{key}/publish", {"version": 1})
    paused = api.call("POST", f"/collectors/{key}/pause", {"version": 1})
    assert (first["live_revision"], paused["enabled"]) == (1, False)

    again = api.call("POST", f"/collectors/{key}/publish", {"version": 1})

    assert (again["live_revision"], again["enabled"]) == (1, True)
    (revision,) = api.call("GET", f"/collectors/{key}/revisions")["items"]
    assert (revision["revision"], revision["definition"]["path"]) == (1, "/v1/builds")
    assert (
        api.keen.db.query(IntegrationRevision).filter_by(collector_id=key).count() == 1
    )
