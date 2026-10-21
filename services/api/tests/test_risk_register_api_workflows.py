"""Conventional risk register workflows through the assembled application.

Ratings, treatment, thresholds, the reusable library and CSV import/export are
exercised with real sign-in, CSRF and permission resolution.
"""

import csv
import io
import uuid

import pytest
from app.db.models import (
    EntityChangelog,
    Risk,
    RiskAsset,
    RiskCategory,
    RiskControlLink,
    RiskLibraryEntry,
)

# Imported fixtures: the assembled application and a signed-in risk manager
# and reader (see tests/test_risk_api_workflows.py).
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_risk_api_workflows import (
    HOSTILE,
    _control,
    _create,
    _org_node,
    _send,
    reg,  # noqa: F401
)

COLUMNS = (
    "asset,category,subcategory,threat_summary,risk_types,owner_username,"
    "owner_role_id,register_likelihood,register_impact,"
    "register_residual_likelihood,register_residual_impact,treatment_strategy,"
    "treatment_status,treatment_plan,treatment_due_at,controls"
)


def _rating(**overrides):
    body = {
        "register_likelihood": 5,
        "register_impact": 4,
        "register_residual_likelihood": 2,
        "register_residual_impact": 1,
        "treatment_strategy": "mitigate",
        "treatment_status": "in_progress",
        "treatment_plan": "Encrypt the exports",
        "treatment_due_at": "2026-12-31",
    }
    body.update(overrides)
    return body


def _csv(*rows, header=COLUMNS):
    return "\n".join([header, *rows]) + "\n"


def _import(reg, text, *, status=200, framework="A", raw=None):  # noqa: F811
    data = raw if raw is not None else text.encode("utf-8")
    return reg.manage(
        "POST",
        "/v1/risks/register/import.csv",
        status=status,
        params={"framework": framework},
        files={"file": ("risks.csv", data, "text/csv")},
    )


def _export(reg, framework="A"):  # noqa: F811
    response = reg.keen.client(reg.reader).get(
        "/v1/risks/register/export.csv", params={"framework": framework}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "keen-risk-register.csv" in response.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(response.text)))


def test_register_routes_follow_the_risk_permissions(reg):  # noqa: F811
    app = reg.keen
    risk = _create(reg)
    entry = reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "Template", "risk_types": ["Integrity"]},
        status=201,
    )
    reads = (
        "/v1/risks/register",
        "/v1/risks/register/heatmap",
        "/v1/risks/register/export.csv",
        "/v1/risks/library",
    )
    anonymous = app.client()
    nobody = app.client(app.user())
    reader = app.client(reg.reader)
    for path in reads:
        assert anonymous.get(path).status_code == 401, path
        assert nobody.get(path).status_code == 403, path
        assert reader.get(path).status_code == 200, path

    writes = (
        ("PATCH", f"/v1/risks/{risk['id']}/register", _rating(), {}),
        (
            "PATCH",
            "/v1/risks/register/settings",
            {"low_max": 3, "moderate_max": 8, "high_max": 15},
            {},
        ),
        (
            "POST",
            "/v1/risks/library",
            {"name": "Other", "risk_types": ["Integrity"]},
            {},
        ),
        ("DELETE", f"/v1/risks/library/{entry['id']}", None, {}),
        (
            "POST",
            "/v1/risks/register/import.csv",
            None,
            {
                "files": {
                    "file": ("r.csv", _csv("A,B,C,D,Integrity,,,,,,,,,,,").encode())
                }
            },
        ),
    )
    for method, path, body, extra in writes:
        reg.read(method, path, body, status=403, **extra)
        _send(nobody, method, path, body, status=403, **extra)

    assert reg.db.query(Risk).count() == 1
    assert reg.db.query(RiskLibraryEntry).count() == 1
    unchanged = reg.read("GET", "/v1/risks/register", params={"framework": "A"})
    assert unchanged["thresholds"] == {"low_max": 4, "moderate_max": 9, "high_max": 16}
    assert unchanged["items"][0]["treatment_status"] == "open"


# --- Ratings and treatment --------------------------------------------------------


def test_ratings_and_treatment_update_scores_and_the_changelog(reg):  # noqa: F811
    risk = _create(reg)
    path = f"/v1/risks/{risk['id']}/register"

    rated = reg.manage("PATCH", path, _rating(treatment_plan=f"  {HOSTILE}  "))
    assert (rated["register_inherent_score"], rated["register_residual_score"]) == (
        20,
        2,
    )
    assert (rated["risk_score"], rated["residual_risk_score"]) == (20, 2)
    assert (rated["vulnerability_score"], rated["impact_score"]) == (5, 4)
    assert (
        rated["residual_vulnerability_score"],
        rated["residual_impact_score"],
    ) == (2, 1)
    assert rated["threat_score"] == 1
    assert rated["treatment_strategy"] == "mitigate"
    assert rated["treatment_status"] == "in_progress"
    assert rated["treatment_plan"] == HOSTILE
    assert rated["treatment_due_at"] == "2026-12-31"

    # Clearing the ratings returns the risk to "needs rating".
    cleared = reg.manage(
        "PATCH",
        path,
        _rating(
            register_likelihood=None,
            register_impact=None,
            register_residual_likelihood=None,
            register_residual_impact=None,
            treatment_strategy="",
            treatment_status="open",
            treatment_due_at=None,
        ),
    )
    assert cleared["register_inherent_score"] is None
    assert cleared["register_residual_score"] is None
    assert (cleared["risk_score"], cleared["residual_risk_score"]) == (0, 0)
    assert cleared["treatment_due_at"] is None

    for body, status, detail in (
        (_rating(treatment_strategy="ignore"), 400, "Invalid treatment strategy"),
        (_rating(treatment_status="done"), 400, "Invalid treatment status"),
        (_rating(register_impact=6), 422, None),
        (_rating(treatment_due_at="31/12/2026"), 422, None),
        (_rating(treatment_plan="x" * 20001), 422, None),
    ):
        response = reg.manage("PATCH", path, body, status=status)
        if detail:
            assert response["detail"] == detail
    reg.manage("PATCH", f"/v1/risks/{uuid.uuid4()}/register", _rating(), status=404)
    reg.manage("PATCH", "/v1/risks/not-a-uuid/register", _rating(), status=422)

    current = reg.read("GET", "/v1/risks/register", params={"framework": "A"})
    assert current["items"][0]["register_likelihood"] is None

    entries = (
        reg.db.query(EntityChangelog)
        .filter_by(entity_id=uuid.UUID(risk["id"]), action="updated")
        .all()
    )
    assert {entry.request_path for entry in entries} == {path}
    assert len(entries) == 2
    fields = {change["field"] for entry in entries for change in entry.changes}
    assert {"register_likelihood", "treatment_plan", "treatment_status"} <= fields


def test_register_lists_risks_with_thresholds_and_a_heatmap(reg):  # noqa: F811
    first = _create(reg, asset_name="Payroll")
    second = _create(reg, asset_name="Website")
    unrated = _create(reg, asset_name="Kiosk")
    reg.manage("PATCH", f"/v1/risks/{first['id']}/register", _rating())
    reg.manage(
        "PATCH",
        f"/v1/risks/{second['id']}/register",
        _rating(register_residual_likelihood=None),
    )
    reg.manage(
        "PATCH",
        f"/v1/risks/{unrated['id']}/register",
        _rating(
            register_likelihood=None,
            register_impact=None,
            register_residual_likelihood=None,
            register_residual_impact=None,
        ),
    )

    listed = reg.read("GET", "/v1/risks/register", params={"framework": "A"})
    assert listed["total"] == 3
    assert [item["id"] for item in listed["items"]] == [
        unrated["id"],
        second["id"],
        first["id"],
    ]  # most recently updated first
    assert listed["thresholds"] == {"low_max": 4, "moderate_max": 9, "high_max": 16}
    second_item = listed["items"][1]
    assert second_item["register_residual_score"] is None
    assert second_item["framework"] == "A"
    page = reg.read(
        "GET",
        "/v1/risks/register",
        params={"framework": "A", "limit": 1, "offset": 1},
    )
    assert [item["id"] for item in page["items"]] == [second["id"]]
    clamped = reg.read(
        "GET",
        "/v1/risks/register",
        params={"framework": "A", "limit": 0, "offset": -3},
    )
    assert (clamped["limit"], clamped["offset"]) == (1, 0)
    reg.read("GET", "/v1/risks/register", params={"framework": "A B"}, status=400)

    heatmap = reg.read("GET", "/v1/risks/register/heatmap")
    assert heatmap["inherent"] == [{"likelihood": 5, "impact": 4, "count": 2}]
    assert heatmap["residual"] == [{"likelihood": 2, "impact": 1, "count": 1}]
    # The thresholds that band the cells travel with the counts.
    assert heatmap["thresholds"] == {"low_max": 4, "moderate_max": 9, "high_max": 16}

    saved = reg.manage(
        "PATCH",
        "/v1/risks/register/settings",
        {"low_max": 5, "moderate_max": 10, "high_max": 20},
    )
    assert saved == {"low_max": 5, "moderate_max": 10, "high_max": 20}
    again = reg.manage(
        "PATCH",
        "/v1/risks/register/settings",
        {"low_max": 3, "moderate_max": 8, "high_max": 15},
    )
    assert again == {"low_max": 3, "moderate_max": 8, "high_max": 15}
    for body, status in (
        ({"low_max": 9, "moderate_max": 9, "high_max": 16}, 400),
        ({"low_max": 4, "moderate_max": 17, "high_max": 16}, 400),
        ({"low_max": 0, "moderate_max": 9, "high_max": 16}, 422),
        ({"low_max": 4, "moderate_max": 9, "high_max": 25}, 422),
    ):
        response = reg.manage(
            "PATCH", "/v1/risks/register/settings", body, status=status
        )
        if status == 400:
            assert (
                response["detail"] == "Thresholds must increase from low through high"
            )
    thresholds = reg.read("GET", "/v1/risks/register", params={"framework": "A"})[
        "thresholds"
    ]
    assert thresholds == {"low_max": 3, "moderate_max": 8, "high_max": 15}


# --- Reusable library ----------------------------------------------------------------


def test_library_entries_capture_reusable_scenarios(reg):  # noqa: F811
    keen_af = "KEEN-AF:1.0"
    control = _control(reg.db, "AST-1", framework=keen_af)
    _control(reg.db, "AST-2", framework=keen_af)
    source = _create(reg, framework=keen_af, controls=[str(control.id), "AST-2"])

    created = reg.manage(
        "POST",
        "/v1/risks/library",
        {
            "name": f"  {HOSTILE}  ",
            "threat_summary": "  Export of salary records  ",
            "risk_types": ["integrity", "Integrity", "confidentiality"],
            "treatment_guidance": "  Encrypt exports  ",
            "suggested_assessment": {
                "asset": "Payroll database",
                "inherent_likelihood": 4,
                "residual_impact": 2,
                "auditor_notes": "not kept",
            },
            "source_risk_id": source["id"],
        },
        status=201,
    )
    assert created["name"] == HOSTILE
    assert created["threat_summary"] == "Export of salary records"
    assert created["treatment_guidance"] == "Encrypt exports"
    assert created["risk_types"] == ["Integrity", "Confidentiality"]
    assert created["suggested_assessment"] == {
        "asset": "Payroll database",
        "inherent_likelihood": 4,
        "residual_impact": 2,
        "keen_af_control_refs": ["AST-1", "AST-2"],
    }

    plain = reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "Backup failure", "risk_types": ["Availability"]},
        status=201,
    )
    assert plain["suggested_assessment"] == {}
    duplicate = reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "BACKUP FAILURE", "risk_types": ["Availability"]},
        status=409,
    )
    assert duplicate["detail"] == "Library item already exists"
    missing = reg.manage(
        "POST",
        "/v1/risks/library",
        {
            "name": "Orphan",
            "risk_types": ["Availability"],
            "source_risk_id": str(uuid.uuid4()),
        },
        status=400,
    )
    assert missing["detail"] == "Source risk not found"
    reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "Unsafe", "risk_types": ["Safety"]},
        status=400,
    )
    # A template for a general register entry carries no CIA associations.
    general = reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "Key person leaves", "risk_types": []},
        status=201,
    )
    assert (general["method"], general["risk_types"]) == ("risk", [])
    reg.manage(
        "POST",
        "/v1/risks/library",
        {"name": "", "risk_types": ["Integrity"]},
        status=422,
    )
    # PESTLE(E) templates record their type and lens and never CIA types.
    pestle = reg.manage(
        "POST",
        "/v1/risks/library",
        {
            "name": "New privacy regulation",
            "method": "pestle",
            "pestle_type": "legal",
            "lens": "external",
            "risk_types": ["Confidentiality"],
            "threat_summary": "Legislation changes retention duties",
        },
        status=201,
    )
    assert (pestle["method"], pestle["pestle_type"], pestle["lens"]) == (
        "pestle",
        "Legal",
        "External",
    )
    assert pestle["risk_types"] == []
    for body, detail in (
        ({"method": "swot"}, "method must be risk or pestle"),
        ({"method": "pestle", "pestle_type": "Cosmic"}, "type must be a valid PESTLE"),
        ({"method": "pestle", "pestle_type": "Legal"}, "lens is required"),
        (
            {"method": "pestle", "pestle_type": "Legal", "lens": "Sideways"},
            "lens must be Internal or External",
        ),
    ):
        refused = reg.manage(
            "POST", "/v1/risks/library", {"name": "Bad", **body}, status=400
        )
        assert detail in refused["detail"], body

    names = [item["name"] for item in reg.read("GET", "/v1/risks/library")["items"]]
    assert sorted(names) == sorted(
        ["Backup failure", "Key person leaves", "New privacy regulation", HOSTILE]
    )
    # The library can be read for one method at a time.
    assert [
        item["name"]
        for item in reg.read("GET", "/v1/risks/library", params={"method": "pestle"})[
            "items"
        ]
    ] == ["New privacy regulation"]
    reg.read("GET", "/v1/risks/library", params={"method": "swot"}, status=400)

    assert reg.manage("DELETE", f"/v1/risks/library/{plain['id']}", status=204) is None
    reg.manage("DELETE", f"/v1/risks/library/{plain['id']}", status=404)
    reg.manage("DELETE", "/v1/risks/library/not-a-uuid", status=422)
    assert sorted(
        item["id"] for item in reg.read("GET", "/v1/risks/library")["items"]
    ) == sorted([created["id"], general["id"], pestle["id"]])


# --- CSV import and export -----------------------------------------------------------


def test_register_csv_import_and_export_round_trip(reg):  # noqa: F811
    _control(reg.db, "A.5.1")
    _control(reg.db, "A.5.2")
    owner = reg.keen.user()
    role = _org_node(reg.db, "Records office")
    text = _csv(
        f"Payroll database,Data,Databases,Salary export,Confidentiality; integrity,"
        f"{owner.username},,5,4,2,1,mitigate,in_progress,Encrypt exports,"
        f"2026-12-31,A.5.2;A.5.1",
        f'Website,Software,Web,"Defacement, then outage",Availability,,{role.id},'
        f",,,,,,,,",
    )
    # A byte-order mark from spreadsheet exports is accepted.
    assert _import(reg, "", raw=b"\xef\xbb\xbf" + text.encode()) == {"created": 2}

    listed = reg.read("GET", "/v1/risks/register", params={"framework": "A"})
    by_asset = {item["asset"]: item for item in listed["items"]}
    payroll = by_asset["Payroll database"]
    assert payroll["risk_types"] == ["Confidentiality", "Integrity"]
    assert payroll["risk_owner"]["id"] == str(owner.id)
    assert (payroll["register_inherent_score"], payroll["register_residual_score"]) == (
        20,
        2,
    )
    assert payroll["treatment_due_at"] == "2026-12-31"
    assert [c["ref"] for c in payroll["controls"]] == ["A.5.1", "A.5.2"]
    website = by_asset["Website"]
    assert website["risk_owner"]["type"] == "role"
    assert website["threat_summary"] == "Defacement, then outage"
    assert website["register_inherent_score"] is None
    assert website["treatment_status"] == "open"
    assert website["risk_score"] == 0

    exported = {row["asset"]: row for row in _export(reg)}
    assert exported["Payroll database"] == {
        "asset": "Payroll database",
        "category": "Data",
        "subcategory": "Databases",
        "threat_summary": "Salary export",
        "risk_types": "Confidentiality;Integrity",
        "owner_username": owner.username,
        "owner_role_id": "",
        "register_likelihood": "5",
        "register_impact": "4",
        "register_residual_likelihood": "2",
        "register_residual_impact": "1",
        "treatment_strategy": "mitigate",
        "treatment_status": "in_progress",
        "treatment_plan": "Encrypt exports",
        "treatment_due_at": "2026-12-31",
        "controls": "A.5.1;A.5.2",
    }
    assert exported["Website"]["owner_role_id"] == str(role.id)
    assert exported["Website"]["register_likelihood"] == ""
    # Controls are exported for the requested framework only.
    assert {row["controls"] for row in _export(reg, framework="B")} == {""}

    # The exported file imports again unchanged.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS.split(","))
    writer.writeheader()
    writer.writerows(exported.values())
    assert _import(reg, buffer.getvalue()) == {"created": 2}
    assert reg.db.query(Risk).count() == 4
    assert reg.db.query(RiskAsset).count() == 2


def test_imported_risks_record_their_creation_at_the_import_time(reg):  # noqa: F811
    control = _control(reg.db, "A.5.1")
    text = _csv(
        f"Payroll database,Data,Databases,{HOSTILE},Confidentiality,,,5,4,2,1,"
        ",,,,A.5.1",
        "Website,Software,Web,Defacement,Availability,,,,,,,,,,,",
    )
    assert _import(reg, text) == {"created": 2}

    reg.db.expire_all()
    risks = reg.db.query(Risk).all()
    entries = reg.db.query(EntityChangelog).filter_by(entity_type="risk").all()
    assert sorted(e.entity_id for e in entries) == sorted(r.id for r in risks)
    assert {(e.action, e.request_method, e.request_path) for e in entries} == {
        ("created", "POST", "/v1/risks/register/import.csv")
    }
    assert {e.changed_by_user_id for e in entries} == {reg.manager.id}
    # The import is saved at once, so the risks and their history share its time.
    moments = {e.changed_at for e in entries}
    moments |= {r.created_at for r in risks} | {r.updated_at for r in risks}
    assert len(moments) == 1

    payroll = next(r for r in risks if r.threat_summary == HOSTILE)
    entry = next(e for e in entries if e.entity_id == payroll.id)
    assert entry.after_state["threat_summary"] == HOSTILE
    assert entry.after_state["asset"] == "Payroll database"
    assert [c["id"] for c in entry.after_state["controls"]] == [str(control.id)]
    history = reg.read("GET", f"/v1/risks/{payroll.id}/changelog")["items"]
    assert [item["action"] for item in history] == ["created"]


def test_exported_cells_cannot_become_spreadsheet_formulas(reg):  # noqa: F811
    _create(
        reg,
        asset_name='=HYPERLINK("https://attacker.example","x")',
        category_name="@SUM(1)",
        subcategory_name="-1+1",
        threat_summary="+cmd",
        note=HOSTILE,
    )
    (row,) = _export(reg)
    assert row["asset"] == '\'=HYPERLINK("https://attacker.example","x")'
    assert row["category"] == "'@SUM(1)"
    assert row["subcategory"] == "'-1+1"
    assert row["threat_summary"] == "'+cmd"


@pytest.mark.parametrize(
    "text,detail",
    [
        ("asset,category\nA,B\n", "row header: CSV requires asset, category"),
        (_csv(), "row header: CSV contains no risks"),
        (_csv("A,B,C,D,Integrity,,,,,,,,,,,,extra"), "row 2: Row 2: extra columns"),
        (
            _csv("A,B,C,D,Integrity,,,6,,,,,,,,"),
            "row 2: register_likelihood must be 1–5",
        ),
        (_csv("A,B,C,D,Integrity,,,high,,,,,,,,"), "row 2: invalid literal"),
        (
            _csv("A,B,C,D,Integrity,,,,,,,ignore,,,,"),
            "row 2: Invalid treatment strategy or status",
        ),
        (
            _csv("A,B,C,D,Integrity,,,,,,,,done,,,"),
            "row 2: Invalid treatment strategy or status",
        ),
        (_csv("A,B,C,D,Integrity,,,,,,,,,,31/12/2026,"), "row 2: Invalid isoformat"),
        (
            _csv("A,B,C,D,Integrity,,,,,,,,,,,NOPE"),
            "row 2: Unknown control for A: NOPE",
        ),
        (
            _csv("A,B,C,D,Integrity,nobody-by-this-name,,,,,,,,,,"),
            "row 2: Unknown or inactive risk owner",
        ),
        (_csv("A,B,C,D,Integrity,,not-a-uuid,,,,,,,,,"), "row 2:"),
        (
            _csv("A,B,C,D,Integrity,,,,,,,,,,,", "E,F,G,H,Safety,,,,,,,,,,,"),
            "row 3: risk_types must contain only",
        ),
        (_csv(",B,C,D,Integrity,,,,,,,,,,,"), "row 2: asset is required"),
        (
            _csv(*["A,B,C,D,Integrity,,,,,,,,,,," for _ in range(1001)]),
            "row header: Import at most 1000 risks per file",
        ),
    ],
)
def test_register_import_is_all_or_nothing(reg, text, detail):  # noqa: F811
    _create(reg, asset_name="Existing", category_name="Data")
    response = _import(reg, text, status=400)
    assert response["detail"].startswith(f"Import failed at {detail}"), response
    # Nothing from the file is kept, including rows before the failure and
    # their creation history.
    assert reg.db.query(Risk).count() == 1
    assert reg.db.query(EntityChangelog).filter_by(entity_type="risk").count() == 1
    assert reg.db.query(RiskAsset).count() == 1
    assert reg.db.query(RiskCategory).count() == 1
    assert reg.db.query(RiskControlLink).count() == 0


def test_register_import_rejects_oversized_and_undecodable_files(reg):  # noqa: F811
    big = _csv("A,B,C," + "x" * 2_000_001 + ",Integrity,,,,,,,,,,,")
    response = _import(reg, big, status=413)
    assert response["detail"] == "Risk CSV exceeds 2 MB"
    response = _import(reg, "", raw=b"asset,category\n\xff\xfe\n", status=400)
    assert response["detail"].startswith("Import failed at row header:")
    _import(reg, _csv("A,B,C,D,Integrity,,,,,,,,,,,"), framework="A B", status=400)
    assert reg.db.query(Risk).count() == 0


def test_library_entries_from_risks_without_keen_af_links_carry_no_control_refs(
    reg,  # noqa: F811
):
    """A source risk linked to no KEEN-AF controls suggests no control references."""
    source = _create(reg, framework="KEEN-AF:1.0")

    created = reg.manage(
        "POST",
        "/v1/risks/library",
        {
            "name": "Unlinked scenario",
            "risk_types": ["Integrity"],
            "suggested_assessment": {"inherent_impact": 3},
            "source_risk_id": source["id"],
        },
        status=201,
    )

    assert created["suggested_assessment"] == {"inherent_impact": 3}
