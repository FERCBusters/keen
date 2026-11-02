"""Each control's standing against its requirements, for the Controls page.

Policies and processes count the ISMS documents linked to a control; measures
count its linked effectiveness measures and how their latest results stand
against their targets. The Controls page combines these with evidence counts.
"""

import pytest
from app.db.models import ControlItem, Framework
from app.services import features

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401


@pytest.fixture
def lab(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    controls = {
        ref: ControlItem(framework_slug="A", ref=ref, type="annex_control", title=ref)
        for ref in ("A.1", "A.2", "A.3", "A.4")
    }
    controls["B.1"] = ControlItem(framework_slug="B", ref="B.1", type="annex_control")
    db.add_all(controls.values())
    db.commit()
    manager = keen.client(keen.user("isms.manage"))

    def post(path, body, framework="A"):
        response = manager.post(
            path, params={"framework": framework}, json=body, headers=unsafe(manager)
        )
        assert response.status_code == 200, response.text
        return response.json()

    def ids(*refs):
        return [str(controls[ref].id) for ref in refs]

    def measure(summary, refs, results, framework="A", **target):
        row = post(
            "/v1/isms/effectiveness-measures",
            {
                "summary": summary,
                "controls": ids(*refs),
                **(
                    {
                        "target_value": 90,
                        "target_unit": "%",
                        "threshold_operator": "gte",
                    }
                    | target
                ),
            },
            framework,
        )
        for day, value in enumerate(results, start=1):
            post(
                f"/v1/isms/effectiveness-measures/{row['id']}/metrics",
                {
                    "metric_value": value,
                    "metric_unit": "%",
                    "recorded_at": f"2026-10-0{day}T09:00:00",
                },
                framework,
            )

    for title in ("Access policy", "Joiners and leavers"):
        post("/v1/isms/documents", {"title": title, "controls": ids("A.1", "A.2")})
    post("/v1/isms/documents", {"title": "Backups", "controls": ids("A.3")})
    post("/v1/isms/documents", {"title": "Elsewhere", "controls": ids("B.1")}, "B")
    # A.1 meets its target on the latest result; A.2 fell outside and has a
    # measure without results; A.3 has a result that cannot be compared.
    measure("Reviews on time", ["A.1", "A.2"], [70, 95])
    measure("Leavers removed", ["A.2"], [99, 50])
    measure("Restores tested", ["A.2"], [])
    measure("Tests passed", ["A.3"], [95], target_unit="tests")
    measure("Other framework", ["B.1"], [10], framework="B")
    return keen, controls


def test_each_control_has_its_policies_and_measure_standing(lab):
    app, controls = lab
    reader = app.client(app.user("isms.read"))
    response = reader.get("/v1/isms/control-requirements", params={"framework": "A"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["framework"], body["policies"], body["measures"]) == ("A", True, True)
    by_ref = {
        ref: body["items"].get(str(control.id))
        for ref, control in controls.items()
        if ref != "B.1"
    }
    assert by_ref == {
        "A.1": {"policies": 2, "measures": 1, "met": 1, "judged": 1, "unrecorded": 0},
        "A.2": {"policies": 2, "measures": 3, "met": 1, "judged": 2, "unrecorded": 1},
        # A result in another unit than the target cannot be judged.
        "A.3": {"policies": 1, "measures": 1, "met": 0, "judged": 0, "unrecorded": 0},
        "A.4": None,
    }
    assert str(controls["B.1"].id) not in body["items"]


def test_parts_switched_off_are_left_out_and_others_are_refused(lab):
    app, controls = lab
    features.update(app.db, app.user(role="admin"), 0, {"documents": False})
    reader = app.client(app.user("isms.read"))
    body = reader.get("/v1/isms/control-requirements", params={"framework": "A"}).json()
    assert (body["policies"], body["measures"]) == (False, True)
    assert body["items"][str(controls["A.1"].id)]["policies"] == 0
    stranger = app.client(app.user("events.read"))
    refused = stranger.get("/v1/isms/control-requirements", params={"framework": "A"})
    assert refused.status_code == 403
    assert (
        reader.get(
            "/v1/isms/control-requirements", params={"framework": "A B"}
        ).status_code
        == 400
    )
