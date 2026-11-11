"""Importing many risks or SWOT items at once: checked like single saves, kept
all or none, and nothing written by a dry run, its diary entries included."""

import uuid

import pytest
from app.db.models import (
    ControlItem,
    EntityChangelog,
    Event,
    Framework,
    IsmsSwot,
    IsmsVendor,
    Risk,
    User,
)
from app.services import record_imports
from app.services.errors import DomainError
from app.services.isms import link_diary
from app.services.record_imports import import_rows, row_payload
from app.services.risks import RiskUpsertPayload
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from tests.test_assembled_authority_regressions import keen as keen  # noqa: F401
from tests.test_assembled_authority_regressions import unsafe


def _risk(**overrides):
    return {
        "asset_name": "Payroll database",
        "category_name": "Data",
        "subcategory_name": "Databases",
        "risk_types": ["Confidentiality"],
        "threat_summary": "Unauthorised export of salary records",
        "register_likelihood": 3,
        "register_impact": 4,
        "register_residual_likelihood": 2,
        "register_residual_impact": 2,
        **overrides,
    }


def _post(client, path, body, status=200):
    answer = client.post(path, json=body, headers=unsafe(client))
    assert answer.status_code == status, answer.text
    return answer.json()


def _files(path):
    return sorted(p for p in path.rglob("*") if p.is_file())


@pytest.fixture
def imports(keen, tmp_path):  # noqa: F811 - fixture imported above
    keen.db.add(Framework(slug="A", name="A"))
    keen.db.add(ControlItem(framework_slug="A", ref="A.5.1", type="annex_control"))
    keen.db.commit()
    keen.artifacts = tmp_path
    return keen


def test_a_risk_import_is_checked_first_and_kept_all_or_none(imports):
    keen = imports
    manager = keen.client(keen.user("risk.manage"))
    reader = keen.client(keen.user("risk.read"))
    path = "/v1/risks/import?framework=A"
    rows = [
        _risk(controls=["A.5.1"]),
        _risk(threat_summary="Ransomware", treatment_strategy="mitigate"),
    ]
    refused = reader.post(path, json={"rows": rows}, headers=unsafe(reader))
    assert refused.status_code == 403
    before = _files(keen.artifacts)

    # A dry run, the default, checks each row and keeps nothing.
    preview = _post(manager, path, {"rows": rows})
    assert preview["dry_run"] is True
    assert (preview["accepted"], preview["refused"], preview["created"]) == (2, 0, 0)
    first = preview["rows"][0]
    assert first["ok"] and first["row"] == 1
    assert first["asset"] == "Payroll database" and first["controls"] == ["A.5.1"]
    assert first["category"] == "Data" and first["subcategory"] == "Databases"
    assert first["risk_score"] == 12 and first["treatment_status"] == "open"
    assert keen.db.query(Risk).count() == 0
    assert keen.db.query(EntityChangelog).count() == 0
    assert keen.db.query(Event).count() == 0
    assert _files(keen.artifacts) == before

    # One refused row keeps every row out, even when saving was asked for.
    bad = [*rows, _risk(register_likelihood=9), _risk(id=str(uuid.uuid4()))]
    answer = _post(manager, path, {"rows": bad, "dry_run": False})
    assert (answer["accepted"], answer["refused"], answer["created"]) == (2, 2, 0)
    assert answer["rows"][2]["ok"] is False
    assert "register_likelihood" in answer["rows"][2]["error"]
    assert answer["rows"][3]["error"].startswith("Unknown field: id. Fields")
    assert keen.db.query(Risk).count() == 0
    assert _files(keen.artifacts) == before

    # Saved: every row, with its history and one diary entry for its links.
    saved = _post(manager, path, {"rows": rows, "dry_run": False})
    assert (saved["dry_run"], saved["created"]) == (False, 2)
    keen.db.expire_all()
    assert keen.db.query(Risk).count() == 2
    assert keen.db.query(EntityChangelog).filter_by(entity_type="risk").count() == 2
    assert _files(keen.artifacts) != before


def test_import_requests_are_bounded_and_strict(imports):
    keen = imports
    manager = keen.client(keen.user("risk.manage", "isms.manage"))
    for body in (
        {"rows": []},
        {"rows": [_risk()] * 501},
        {"rows": [_risk()], "extra": True},
        {"rows": ["not a row"]},
    ):
        refused = manager.post("/v1/risks/import", json=body, headers=unsafe(manager))
        assert refused.status_code == 422, body


def test_an_imported_risk_is_refused_for_what_a_single_save_refuses(imports):
    keen = imports
    manager = keen.client(keen.user("risk.manage"))
    answer = _post(
        manager,
        "/v1/risks/import?framework=A",
        {
            "rows": [
                _risk(affected=[{"new_vendor": "Acme"}]),
                _risk(affected=[{"person_id": str(uuid.uuid4())}]),
            ]
        },
    )
    assert [row["ok"] for row in answer["rows"]] == [True, False]
    assert answer["rows"][1]["error"] == "Unknown person"
    # The dry run named a new vendor and added none.
    assert keen.db.query(IsmsVendor).count() == 0


def test_a_swot_import_is_checked_first_and_kept_all_or_none(imports):
    keen = imports
    owner = keen.user("isms.read")
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))
    path = "/v1/isms/swot/import?framework=A"
    rows = [
        {"title": "Skilled staff", "kind": "strength", "controls": ["A.5.1"]},
        {
            "title": "Supplier outage",
            "kind": "threat",
            "significance": 5,
            "owner_user_id": str(owner.id),
        },
    ]
    refused = reader.post(path, json={"rows": rows}, headers=unsafe(reader))
    assert refused.status_code == 403
    before = _files(keen.artifacts)

    preview = _post(manager, path, {"rows": rows})
    assert (preview["accepted"], preview["created"]) == (2, 0)
    assert preview["rows"][1] == {
        "row": 2,
        "ok": True,
        "title": "Supplier outage",
        "kind": "threat",
        "significance": 5,
    }
    assert keen.db.query(IsmsSwot).count() == 0
    assert _files(keen.artifacts) == before

    bad = [
        *rows,
        {"title": "Linked", "kind": "threat", "links": []},
        {"title": "", "kind": "threat"},
        {"title": "Odd", "kind": "unknown"},
    ]
    answer = _post(manager, path, {"rows": bad, "dry_run": False})
    assert (answer["accepted"], answer["refused"], answer["created"]) == (2, 3, 0)
    errors = [row.get("error") for row in answer["rows"]]
    assert errors[2].startswith("Unknown field: links")
    assert errors[3] == "title: String should have at least 1 character"
    assert errors[4].startswith("kind: ")
    assert keen.db.query(IsmsSwot).count() == 0

    saved = _post(manager, path, {"rows": rows, "dry_run": False})
    assert saved["created"] == 2
    keen.db.expire_all()
    assert {row.title for row in keen.db.query(IsmsSwot)} == {
        "Skilled staff",
        "Supplier outage",
    }
    assert _files(keen.artifacts) != before


class _Row(BaseModel):
    name: str
    framework: str = ""


def test_a_row_names_only_the_fields_an_import_sets():
    row = row_payload(_Row, {"name": "x"}, ["name"], {"framework": "A"})
    assert (row.name, row.framework) == ("x", "A")
    with pytest.raises(DomainError, match=r"^Unknown fields: a, b\. Fields"):
        row_payload(_Row, {"name": "x", "b": 1, "a": 2}, ["name"])


def test_a_row_refused_by_the_database_is_reported(imports):
    keen = imports
    calls = []

    def add(row):
        calls.append(row)
        if row["n"] == 2:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        if row["n"] == 3:
            raise DomainError(400, "Refused")
        return {"n": row["n"]}

    answer = import_rows(keen.db, [{"n": 1}, {"n": 2}, {"n": 3}], add, dry_run=False)
    assert [row.get("error") for row in answer["rows"]] == [
        None,
        "The row conflicts with a record Keen already holds",
        "Refused",
    ]
    assert answer["created"] == 0 and len(calls) == 3


def test_a_refused_rows_diary_writes_are_dropped(imports, monkeypatch):
    keen = imports
    user = keen.db.query(User).first() or keen.user()
    first, second = uuid.uuid4(), uuid.uuid4()
    written = []
    monkeypatch.setattr(
        record_imports,
        "write_deferred_link_diary",
        lambda db, pending: written.extend(pending),
    )

    def add(row):
        link_diary.sync_entity_link_diary(keen.db, "risk", row["id"], user)
        if row.get("refuse"):
            raise DomainError(400, "Refused")
        # A record saved twice in one import is written once.
        link_diary.sync_entity_link_diary(keen.db, "risk", row["id"], user)
        return {}

    rows = [{"id": first}, {"id": second, "refuse": True}]
    answer = import_rows(keen.db, rows, add, dry_run=False)
    assert answer["refused"] == 1 and written == []
    answer = import_rows(keen.db, rows[:1], add, dry_run=False)
    assert answer["created"] == 1
    assert written == [("risk", first, user, None)] * 2
    assert link_diary._DEFERRED not in keen.db.info


def test_deferred_diary_writes_are_made_once_each(imports, monkeypatch):
    keen = imports
    made = []
    monkeypatch.setattr(
        link_diary, "_record_type", lambda kind: made.append(kind) or kind
    )
    record = uuid.uuid4()
    with link_diary.deferred_link_diary(keen.db) as pending:
        link_diary.sync_entity_link_diary(keen.db, "risk", record, None, actor="x")
        link_diary.sync_entity_link_diary(keen.db, "risk", record, None, actor="x")
        assert made == [] and len(pending) == 2
    assert link_diary._DEFERRED not in keen.db.info

    def stop(kind):
        made.append(kind)
        raise RuntimeError("stop")

    monkeypatch.setattr(link_diary, "_record_type", stop)
    with pytest.raises(RuntimeError):
        link_diary.write_deferred_link_diary(keen.db, pending)
    assert made == ["risk"]


def test_the_import_payload_names_fields_a_risk_has():
    from app.services.risks import RISK_IMPORT_FIELDS

    assert record_imports.IMPORT_ROWS == 500

    assert set(RISK_IMPORT_FIELDS) <= set(RiskUpsertPayload.model_fields)
    from app.services.isms.swot import SWOT_IMPORT_FIELDS, SwotPayload

    assert set(SWOT_IMPORT_FIELDS) <= set(SwotPayload.model_fields)
    assert not {"links", "expected_version"} & set(SWOT_IMPORT_FIELDS)
