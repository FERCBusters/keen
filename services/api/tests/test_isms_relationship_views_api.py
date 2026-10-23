"""Views of the ISMS records that relate to a control, clause, source or metric.

These are the reverse views that control, clause and home pages read. Records
are created through the ISMS API and read back through the assembled
application, including its ISMS read boundary and feature settings.
"""

import uuid
from datetime import datetime

import pytest
from app.db.models import ControlItem, Event, FrameworkClause, ManagedConfiguration

# Imported fixtures: the assembled application and a populated ISMS.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_isms_overview_api_workflows import _send, isms  # noqa: F401


def _measure(isms, summary, framework="A", **fields):  # noqa: F811
    return isms.manage(
        "POST",
        "/v1/isms/effectiveness-measures",
        {"summary": summary, "description": "Mean days", **fields},
        framework=framework,
    )


def _control(isms, ref):  # noqa: F811
    return isms.db.query(ControlItem).filter_by(framework_slug="A", ref=ref).one()


def test_a_controls_measures_are_those_linked_to_it_in_its_framework(isms):  # noqa: F811
    control = _control(isms, "A.5.2")
    linked = _measure(isms, "Patch latency", controls=[str(control.id)])
    _measure(isms, "Unlinked measure")
    _measure(isms, "Other control", controls=[str(_control(isms, "A.5.10").id)])
    path = f"/v1/controls/{control.id}/effectiveness-measures"

    listed = isms.manage("GET", path)

    assert (listed["framework"], listed["control_id"]) == ("A", str(control.id))
    assert [item["id"] for item in listed["items"]] == [linked["id"]]
    assert listed["items"][0]["summary"] == "Patch latency"
    # The control belongs to framework A; asking under another framework, or
    # for an unknown control, finds nothing.
    assert isms.manage("GET", path, status=404, framework="B")["detail"] == (
        "Control not found"
    )
    isms.manage(
        "GET", f"/v1/controls/{uuid.uuid4()}/effectiveness-measures", status=404
    )
    refused = isms.manage(
        "GET", "/v1/controls/A.5.2/effectiveness-measures", status=400
    )
    assert refused["detail"] == "control_id must be a UUID"


def test_a_clause_lists_the_isms_records_that_reference_it(isms):  # noqa: F811
    clause = (
        isms.db.query(FrameworkClause).filter_by(framework_slug="A", ref="4.10").one()
    )
    path = f"/v1/clauses/{clause.id}/isms"
    reader = isms.keen.client(isms.keen.user("isms.read"))

    related = _send(reader, "GET", path)

    assert related["framework"] == "A" and related["total"] == 1
    assert [item["id"] for item in related["objectives"]] == [isms.objective["id"]]
    assert related["documents"] == related["meetings"] == []
    other = (
        isms.db.query(FrameworkClause).filter_by(framework_slug="A", ref="4.2").one()
    )
    assert _send(reader, "GET", f"/v1/clauses/{other.id}/isms")["total"] == 0
    refused = _send(reader, "GET", "/v1/clauses/4.10/isms", status=400)
    assert refused["detail"] == "clause_id must be a UUID"

    # Records of a disabled feature drop out of the view.
    isms.db.add(
        ManagedConfiguration(name="features", version=1, document={"objectives": False})
    )
    isms.db.commit()
    hidden = _send(reader, "GET", path)
    assert (hidden["objectives"], hidden["total"]) == ([], 0)


def test_a_metric_entry_is_read_with_its_measure_in_its_framework(isms):  # noqa: F811
    measure = _measure(isms, "Backup restore time", target_unit="minutes")
    entry = isms.manage(
        "POST",
        f"/v1/isms/effectiveness-measures/{measure['id']}/metrics",
        {"metric_value": 42, "source_type": "other"},
    )
    path = f"/v1/isms/effectiveness-metrics/{entry['id']}"

    detail = isms.manage("GET", path)

    assert (detail["id"], detail["metric_value"], detail["framework"]) == (
        entry["id"],
        42,
        "A",
    )
    assert detail["measure"]["id"] == measure["id"]
    assert detail["measure"]["summary"] == "Backup restore time"
    missing = isms.manage("GET", path, status=404, framework="B")
    assert missing["detail"] == "Effectiveness metric not found"
    isms.manage("GET", f"/v1/isms/effectiveness-metrics/{uuid.uuid4()}", status=404)


def test_measures_recorded_against_a_source_are_listed_for_it(isms):  # noqa: F811
    # A metric source must be a source Keen has collected, or "other".
    isms.db.add(
        Event(
            source="loki",
            summary="log line",
            external_id="loki-1",
            timestamp=datetime(2026, 1, 1),
        )
    )
    isms.db.commit()
    from_loki = _measure(isms, "Log coverage")
    from_other = _measure(isms, "Manual review")
    elsewhere = _measure(isms, "Other framework", framework="B")
    for measure, source, framework in (
        (from_loki, "loki", "A"),
        (from_other, "other", "A"),
        (elsewhere, "loki", "B"),
    ):
        isms.manage(
            "POST",
            f"/v1/isms/effectiveness-measures/{measure['id']}/metrics",
            {"metric_value": 1, "source_type": source},
            framework=framework,
        )
    invented = isms.manage(
        "POST",
        f"/v1/isms/effectiveness-measures/{from_other['id']}/metrics",
        {"metric_value": 1, "source_type": "made-up-source"},
        status=400,
    )
    assert invented["detail"].startswith("Invalid metric source type")

    listed = isms.manage("GET", "/v1/sources/loki/effectiveness-measures")

    assert (listed["framework"], listed["source"]) == ("A", "loki")
    assert [item["id"] for item in listed["items"]] == [from_loki["id"]]
    blank = isms.manage("GET", "/v1/sources/%20/effectiveness-measures")
    assert (blank["source"], blank["items"]) == ("", [])


@pytest.mark.parametrize(
    "path",
    [
        "/v1/controls/{control}/effectiveness-measures",
        "/v1/clauses/{clause}/isms",
        "/v1/isms/effectiveness-metrics/{control}",
        "/v1/sources/loki/effectiveness-measures",
    ],
)
def test_relationship_views_need_isms_read_access(isms, path):  # noqa: F811
    clause = isms.db.query(FrameworkClause).filter_by(framework_slug="A").first()
    url = path.format(control=_control(isms, "A.5.2").id, clause=clause.id)
    # Risk permissions also read the ISMS, so the outsider holds neither.
    outsider = isms.keen.client(isms.keen.user("events.read", "audits.manage"))

    refused = _send(outsider, "GET", url, status=403)

    assert refused["detail"] == "isms.read permission required"
    _send(isms.keen.client(), "GET", url, status=401)


def test_a_controls_isms_view_groups_each_kind_of_linked_record(isms):  # noqa: F811
    control = _control(isms, "A.5.2")
    ref = [str(control.id)]
    created = {
        "documents": isms.manage(
            "POST", "/v1/isms/documents", {"title": "Access policy", "controls": ref}
        ),
        "org_nodes": isms.manage(
            "POST",
            "/v1/isms/org-nodes",
            {"name": "Access team", "node_type": "team", "controls": ref},
        ),
        "assets": isms.manage(
            "POST",
            "/v1/isms/assets",
            {
                "asset": "Directory",
                "category_name": "Data",
                "subcategory_name": "Identity",
                "controls": ref,
            },
        ),
        "effectiveness_measures": _measure(isms, "Access reviews", controls=ref),
        "meetings": isms.manage(
            "POST",
            "/v1/isms/meetings",
            {"title": "Access review", "date": "2026-05-01", "controls": ref},
        ),
        "swot": isms.manage(
            "POST",
            "/v1/isms/swot",
            {"title": "Shared accounts", "kind": "weakness", "controls": ref},
        ),
    }
    # The same control referenced under framework B stays out of framework A.
    isms.manage(
        "POST",
        "/v1/isms/documents",
        {"title": "Other framework policy", "controls": ref},
        status=400,
        framework="B",
    )

    view = isms.manage("GET", f"/v1/controls/{control.id}/isms")

    assert view["framework"] == "A"
    for group, record in created.items():
        assert [item["id"] for item in view[group]] == [record["id"]], group
    assert view["objectives"] == []
    assert view["total"] == len(created)
    displays = {group: view[group][0].get("display") for group in created}
    assert displays["org_nodes"] == "Access team"
    assert displays["assets"] == "Directory"
    unknown = isms.manage("GET", "/v1/controls/not-a-uuid/isms", status=400)
    assert unknown["detail"] == "control_id must be a UUID"
