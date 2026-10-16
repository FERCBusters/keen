"""People, personnel assurance and vendor registers through the assembled app.

ISMS readers see the registers; ISMS managers change them. Links to users,
organisation nodes and assets are validated, and rejected writes leave the
stored records unchanged.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import (
    IsmsOrgNode,
    IsmsPerson,
    IsmsPersonAssurance,
    IsmsVendor,
    ManagedConfiguration,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _send(client, method, path, body=None, status=200):
    response = client.request(
        method,
        path,
        headers=unsafe(client) if method != "GET" else {},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def registers(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    category = RiskCategory(name="Hardware")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(category_id=category.id, name="Devices")
    db.add(subcategory)
    db.flush()
    laptop, phone, server = (
        RiskAsset(name=name, category_id=category.id, subcategory_id=subcategory.id)
        for name in ("Laptop", "Phone", "Server")
    )
    team = IsmsOrgNode(name="Operations", node_type="team")
    db.add_all([laptop, phone, server, team])
    db.commit()
    manager = keen.user("isms.manage")
    reader = keen.user("isms.read")
    manager_client = keen.client(manager)
    reader_client = keen.client(reader)
    return SimpleNamespace(
        keen=keen,
        db=db,
        laptop=laptop,
        phone=phone,
        server=server,
        team=team,
        manager=manager,
        manage=lambda *a, **k: _send(manager_client, *a, **k),
        read=lambda *a, **k: _send(reader_client, *a, **k),
    )


def test_registers_need_isms_read_and_manage(registers):
    app = registers.keen
    person = registers.manage("POST", "/v1/isms/people", {"name": "Ada"}, status=201)
    vendor = registers.manage("POST", "/v1/isms/vendors", {"name": "Acme"}, status=201)
    reads = ("/v1/isms/assurance/meta", "/v1/isms/people", "/v1/isms/vendors")
    anonymous = app.client()
    nobody = app.client(app.user())
    for path in reads:
        assert anonymous.get(path).status_code == 401
        _send(nobody, "GET", path, status=403)
        registers.read("GET", path)
    assurance = {"category": "Training", "name": "Security awareness"}
    writes = (
        ("POST", "/v1/isms/people", {"name": "Eve"}),
        ("PATCH", f"/v1/isms/people/{person['id']}", {"name": "Eve"}),
        ("DELETE", f"/v1/isms/people/{person['id']}", None),
        ("POST", f"/v1/isms/people/{person['id']}/assurances", assurance),
        ("POST", "/v1/isms/vendors", {"name": "Other"}),
        ("PATCH", f"/v1/isms/vendors/{vendor['id']}", {"name": "Other"}),
        ("DELETE", f"/v1/isms/vendors/{vendor['id']}", None),
    )
    for method, path, body in writes:
        registers.read(method, path, body, status=403)
        _send(nobody, method, path, body, status=403)
    assert [p["name"] for p in registers.read("GET", "/v1/isms/people")["items"]] == [
        "Ada"
    ]
    assert [v["name"] for v in registers.read("GET", "/v1/isms/vendors")["items"]] == [
        "Acme"
    ]
    assert registers.db.query(IsmsPersonAssurance).count() == 0


def test_meta_offers_active_users_org_nodes_and_assets(registers):
    retired = registers.keen.user()
    retired.is_active = False
    registers.db.commit()
    meta = registers.read("GET", "/v1/isms/assurance/meta")
    names = [user["name"] for user in meta["users"]]
    assert registers.manager.username in names and retired.username not in names
    assert meta["org_nodes"] == [{"id": str(registers.team.id), "name": "Operations"}]
    assert [asset["name"] for asset in meta["assets"]] == ["Laptop", "Phone", "Server"]


def test_people_link_users_org_nodes_and_assets(registers):
    linked_user = registers.keen.user()
    person = registers.manage(
        "POST",
        "/v1/isms/people",
        {
            "name": f"  {HOSTILE}  ",
            "email": " ada@example.test ",
            "position": " Engineer ",
            "notes": " Night shift ",
            "user_id": str(linked_user.id),
            "org_node_id": str(registers.team.id),
            "asset_ids": [
                str(registers.laptop.id),
                str(registers.laptop.id),
                str(registers.phone.id),
            ],
        },
        status=201,
    )
    assert person["name"] == HOSTILE
    assert (person["email"], person["position"], person["notes"]) == (
        "ada@example.test",
        "Engineer",
        "Night shift",
    )
    assert person["username"] == linked_user.username
    assert person["org_node"] == "Operations"
    assert sorted(person["asset_ids"]) == sorted(
        [str(registers.laptop.id), str(registers.phone.id)]
    )
    assert person["assurances"] == []

    path = f"/v1/isms/people/{person['id']}"
    # The page sends the whole form; clearing a link is an explicit null.
    updated = registers.manage(
        "PATCH",
        path,
        {
            "name": "Ada",
            "email": "",
            "position": "Engineer",
            "notes": "",
            "user_id": None,
            "org_node_id": None,
            "asset_ids": [str(registers.phone.id), str(registers.server.id)],
        },
    )
    assert sorted(updated["asset_ids"]) == sorted(
        [str(registers.phone.id), str(registers.server.id)]
    )
    assert (updated["user_id"], updated["org_node_id"], updated["email"]) == (
        None,
        None,
        "",
    )
    assert updated["position"] == "Engineer"

    second = registers.manage(
        "POST",
        "/v1/isms/people",
        {"name": "bob", "user_id": str(linked_user.id)},
        status=201,
    )
    response = registers.manage(
        "PATCH",
        path,
        {"name": "Ada", "user_id": str(linked_user.id)},
        status=409,
    )
    assert response["detail"] == "This KEEN user is already linked to another person"
    # Re-saving the person who holds the link is allowed.
    registers.manage(
        "PATCH",
        f"/v1/isms/people/{second['id']}",
        {"name": "Bob", "user_id": str(linked_user.id)},
    )

    rejected = (
        ({"name": "   "}, 400, "Name is required"),
        ({"name": ""}, 422, None),
        ({"name": "X", "user_id": str(uuid.uuid4())}, 404, "Record not found"),
        ({"name": "X", "org_node_id": str(uuid.uuid4())}, 404, "Record not found"),
        ({"name": "X", "asset_ids": [str(uuid.uuid4())]}, 400, "Unknown asset"),
        (
            {"name": "X", "asset_ids": [str(uuid.uuid4()) for _ in range(201)]},
            400,
            "Too many linked assets",
        ),
        ({"name": "X", "email": "x" * 257}, 422, None),
    )
    for body, status, detail in rejected:
        response = registers.manage("POST", "/v1/isms/people", body, status=status)
        if detail:
            assert response["detail"] == detail
    assert registers.db.query(IsmsPerson).count() == 2

    names = [p["name"] for p in registers.read("GET", "/v1/isms/people")["items"]]
    assert names == ["Ada", "Bob"]
    registers.manage(
        "PATCH", f"/v1/isms/people/{uuid.uuid4()}", {"name": "X"}, status=404
    )
    registers.manage("DELETE", f"/v1/isms/people/{uuid.uuid4()}", status=404)


def test_assurance_records_are_validated_and_scoped_to_their_person(registers):
    ada = registers.manage("POST", "/v1/isms/people", {"name": "Ada"}, status=201)
    bob = registers.manage("POST", "/v1/isms/people", {"name": "Bob"}, status=201)
    base = f"/v1/isms/people/{ada['id']}/assurances"
    screening = registers.manage(
        "POST",
        base,
        {
            "category": " Screening ",
            "name": " Background check ",
            "status": "met",
            "source_system": " HR ",
            "evidence_url": " https://hr.example/check/1 ",
            "completed_at": "2026-01-10",
            "expires_at": "2027-01-10",
            "notes": f" {HOSTILE} ",
        },
        status=201,
    )
    assert screening["person_id"] == ada["id"]
    assert (screening["category"], screening["name"], screening["notes"]) == (
        "Screening",
        "Background check",
        HOSTILE,
    )
    assert screening["evidence_url"] == "https://hr.example/check/1"
    assert (screening["completed_at"], screening["expires_at"]) == (
        "2026-01-10",
        "2027-01-10",
    )
    training = registers.manage(
        "POST",
        base,
        {
            "category": "Awareness",
            "name": "Phishing course",
            "evidence_url": "/api/v1/artifacts/abc/download",
        },
        status=201,
    )
    assert training["status"] == "pending"

    for body, detail in (
        ({"category": "A", "name": "B", "status": "done"}, "Invalid assurance status"),
        ({"category": " ", "name": "B"}, "Category and name are required"),
        (
            {"category": "A", "name": "B", "evidence_url": "javascript:alert(1)"},
            "Evidence link must be an http(s) URL or an artifact path",
        ),
        (
            {"category": "A", "name": "B", "evidence_url": "/admin.html"},
            "Evidence link must be an http(s) URL or an artifact path",
        ),
        (
            {
                "category": "A",
                "name": "B",
                "completed_at": "2026-02-01",
                "expires_at": "2026-01-01",
            },
            "Expiry precedes completion",
        ),
    ):
        response = registers.manage("POST", base, body, status=400)
        assert response["detail"] == detail
    registers.manage(
        "POST",
        f"/v1/isms/people/{uuid.uuid4()}/assurances",
        {"category": "A", "name": "B"},
        status=404,
    )

    listed = {p["name"]: p for p in registers.read("GET", "/v1/isms/people")["items"]}
    assert [(a["category"], a["name"]) for a in listed["Ada"]["assurances"]] == [
        ("Awareness", "Phishing course"),
        ("Screening", "Background check"),
    ]

    item = f"{base}/{screening['id']}"
    renewed = registers.manage(
        "PATCH",
        item,
        {"category": "Screening", "name": "Background check", "status": "expired"},
    )
    assert renewed["status"] == "expired" and renewed["completed_at"] is None
    # Another person's path cannot reach this record.
    wrong = f"/v1/isms/people/{bob['id']}/assurances/{screening['id']}"
    registers.manage("PATCH", wrong, {"category": "A", "name": "B"}, status=404)
    registers.manage("DELETE", wrong, status=404)
    registers.manage(
        "PATCH", f"{base}/{uuid.uuid4()}", {"category": "A", "name": "B"}, status=404
    )
    assert registers.manage("DELETE", item, status=204) is None
    registers.manage("DELETE", item, status=404)

    # Deleting a person removes their remaining assurance records.
    registers.manage("DELETE", f"/v1/isms/people/{ada['id']}", status=204)
    assert registers.db.query(IsmsPersonAssurance).count() == 0
    assert [p["name"] for p in registers.read("GET", "/v1/isms/people")["items"]] == [
        "Bob"
    ]


def test_vendors_own_assets_and_validate_websites(registers):
    acme = registers.manage(
        "POST",
        "/v1/isms/vendors",
        {
            "name": f" {HOSTILE} ",
            "description": " Hardware supplier ",
            "website": " https://acme.example ",
            "contact": " sales@acme.example ",
            "asset_ids": [str(registers.laptop.id), str(registers.phone.id)],
        },
        status=201,
    )
    assert acme["name"] == HOSTILE
    assert (acme["description"], acme["website"], acme["contact"]) == (
        "Hardware supplier",
        "https://acme.example",
        "sales@acme.example",
    )
    assert sorted(acme["asset_ids"]) == sorted(
        [str(registers.laptop.id), str(registers.phone.id)]
    )

    # Choosing an asset for another vendor moves it.
    globex = registers.manage(
        "POST",
        "/v1/isms/vendors",
        {"name": "Globex", "asset_ids": [str(registers.phone.id)]},
        status=201,
    )
    assert globex["asset_ids"] == [str(registers.phone.id)]
    vendors = {v["name"]: v for v in registers.read("GET", "/v1/isms/vendors")["items"]}
    assert vendors[HOSTILE]["asset_ids"] == [str(registers.laptop.id)]

    for body, status, detail in (
        ({"name": "GLOBEX"}, 409, "Vendor already exists"),
        (
            {"name": "Initech", "website": "javascript:alert(1)"},
            400,
            "Website must use http or https",
        ),
        (
            {"name": "Initech", "website": "ftp://files.example"},
            400,
            "Website must use http or https",
        ),
        ({"name": " "}, 400, "Name is required"),
        ({"name": "Initech", "asset_ids": [str(uuid.uuid4())]}, 400, "Unknown asset"),
    ):
        response = registers.manage("POST", "/v1/isms/vendors", body, status=status)
        assert response["detail"] == detail
    assert registers.db.query(IsmsVendor).count() == 2

    path = f"/v1/isms/vendors/{globex['id']}"
    renamed = registers.manage("PATCH", path, {"name": "globex", "asset_ids": []})
    assert renamed["name"] == "globex" and renamed["asset_ids"] == []
    registers.db.expire_all()
    assert registers.db.get(RiskAsset, registers.phone.id).vendor_id is None
    registers.manage(
        "PATCH", f"/v1/isms/vendors/{uuid.uuid4()}", {"name": "X"}, status=404
    )

    acme_path = f"/v1/isms/vendors/{acme['id']}"
    assert registers.manage("DELETE", acme_path, status=204) is None
    registers.db.expire_all()
    assert registers.db.get(RiskAsset, registers.laptop.id).vendor_id is None
    registers.manage("DELETE", acme_path, status=404)
    assert [v["name"] for v in registers.read("GET", "/v1/isms/vendors")["items"]] == [
        "globex"
    ]


def test_people_and_vendor_registers_follow_feature_settings(registers):
    registers.db.add(
        ManagedConfiguration(name="features", version=1, document={"people": False})
    )
    registers.db.commit()
    response = registers.read("GET", "/v1/isms/people", status=404)
    assert response["detail"] == "This feature is disabled by an administrator."
    registers.manage("POST", "/v1/isms/people", {"name": "Ada"}, status=404)
    registers.read("GET", "/v1/isms/vendors")
    registers.read("GET", "/v1/isms/assurance/meta")
    assert registers.db.query(IsmsPerson).count() == 0
