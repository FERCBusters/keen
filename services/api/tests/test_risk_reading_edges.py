"""Risk ownership, Mitigator subcategories and affected parties at their edges.

Only an active account owns a risk, directly or through its role; the
Mitigator accepts a subcategory without its category and scopes it only when
a category is named; and a risk read while one of its affected parties is
removed lists the parties that remain.
"""

import uuid

import pytest
from app.db.models import (
    InterestedParty,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsPerson,
    IsmsVendor,
    Risk,
    RiskAssetSubcategory,
    RiskCategory,
)
from app.services.risks import _is_risk_owner
from sqlalchemy import event

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_risk_api_workflows import _create, _org_node
from tests.test_risk_api_workflows import reg as reg


def test_only_an_active_account_owns_a_risk(reg):
    db = reg.db
    owner = reg.keen.user()
    member = reg.keen.user()
    role = _org_node(db, "Payroll owners", members=[member])
    owned = db.get(
        Risk, uuid.UUID(_create(reg, risk_owner_user_id=str(owner.id))["id"])
    )
    by_role = db.get(
        Risk, uuid.UUID(_create(reg, risk_owner_role_id=str(role.id))["id"])
    )

    assert _is_risk_owner(owner, owned) is True
    assert _is_risk_owner(member, by_role) is True
    assert _is_risk_owner(member, owned) is False
    assert _is_risk_owner(None, owned) is False
    for account in (owner, member):
        account.is_active = False
    db.commit()
    assert _is_risk_owner(owner, owned) is False
    assert _is_risk_owner(member, by_role) is False


def test_the_mitigator_scopes_a_subcategory_only_to_a_named_category(reg):
    db = reg.db
    data, hardware = RiskCategory(name="Data"), RiskCategory(name="Hardware")
    db.add_all([data, hardware])
    db.flush()
    databases = RiskAssetSubcategory(category_id=data.id, name="Databases")
    db.add(databases)
    db.commit()
    path = "/v1/risks/mitigator/analyse"
    issue = {"issue": "Unencrypted database backups", "framework": "A"}

    # Without a category, the subcategory is found in whichever category holds it.
    analysed = reg.manage("POST", path, {**issue, "subcategory_id": str(databases.id)})
    assert (analysed["framework"], analysed["asset_category"]) == ("A", "")
    scoped = reg.manage(
        "POST",
        path,
        {**issue, "category_id": str(data.id), "subcategory_id": str(databases.id)},
    )
    assert (scoped["framework"], scoped["asset_category"]) == ("A", "Data")
    for body in (
        {**issue, "subcategory_id": str(uuid.uuid4())},
        {**issue, "category_id": str(hardware.id), "subcategory_id": str(databases.id)},
    ):
        refused = reg.manage("POST", path, body, status=400)
        assert refused["detail"] == "Unknown asset subcategory"


def test_a_risk_read_while_its_parties_are_removed_lists_those_that_remain(reg):
    db = reg.db
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("The removals are run on the PostgreSQL connection")
    nature = InterestedPartyNature(name="External")
    customers, regulator = (
        InterestedPartyName(name="Customers"),
        InterestedPartyName(name="Regulator"),
    )
    db.add_all([nature, customers, regulator])
    db.flush()
    gone_party = InterestedParty(name_id=customers.id, nature_id=nature.id)
    kept_party = InterestedParty(name_id=regulator.id, nature_id=nature.id)
    person = IsmsPerson(name="Dana", position="DPO")
    vendor = IsmsVendor(name="Cloud host", website="https://cloud.example")
    db.add_all([gone_party, kept_party, person, vendor])
    db.commit()
    created = _create(
        reg,
        interested_parties=[
            {"interested_party_id": str(gone_party.id)},
            {"interested_party_id": str(kept_party.id)},
            {"person_id": str(person.id)},
            {"vendor_id": str(vendor.id)},
        ],
    )
    assert len(created["interested_parties"]) == 4
    pending = {
        "interested_parties": str(gone_party.id),
        "isms_people": str(person.id),
        "isms_vendors": str(vendor.id),
    }

    def removed_meanwhile(conn, cursor, statement, parameters, context, many):
        # Each record is removed, as another request committing would, just
        # before the risk's party link loads it.
        if not statement.lstrip().startswith("SELECT") or not isinstance(
            parameters, dict
        ):
            return
        values = {str(value) for value in parameters.values()}
        for table, ident in list(pending.items()):
            if f"FROM {table}" in statement and ident in values:
                cursor.execute(f"DELETE FROM {table} WHERE id = %(id)s", {"id": ident})
                del pending[table]

    event.listen(db.get_bind(), "before_cursor_execute", removed_meanwhile)
    try:
        shown = reg.read("GET", f"/v1/risks/{created['id']}")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", removed_meanwhile)
    assert pending == {}
    assert [(p["kind"], p["id"]) for p in shown["interested_parties"]] == [
        ("interested_party", str(kept_party.id))
    ]
