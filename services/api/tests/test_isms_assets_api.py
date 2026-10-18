"""The ISMS asset register through the assembled application.

Assets sit in a category and subcategory, may name a licence and two
organisation chart nodes, and keep one name per subcategory. Licences and
categories are shared with CIA Triad risks. Readers with isms.read browse the
register and isms.manage covers every change.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import IsmsOrgNode, Risk, RiskAsset

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

MANAGE = "isms.manage permission required"


@pytest.fixture
def reg(keen):  # noqa: F811 - fixture imported above
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))

    def call(client, method, path, status=200, **kwargs):
        if method != "GET":
            kwargs.setdefault("headers", unsafe(client))
        response = client.request(method, "/v1/isms/" + path, **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def manage(method, path, body=None, status=200, **params):
        kwargs = {"params": params} if params else {}
        if body is not None:
            kwargs["json"] = body
        return call(manager, method, path, status, **kwargs)

    def category(name, *subcategories):
        made = manage("POST", "asset-categories", {"name": name})
        for sub in subcategories:
            manage(
                "POST", "asset-subcategories", {"name": sub, "category_id": made["id"]}
            )
        return next(c for c in categories() if c["id"] == made["id"])

    def categories():
        return call(reader, "GET", "asset-categories")["items"]

    def asset(name="Payroll database", **body):
        return manage(
            "POST",
            "assets",
            {
                "asset": name,
                "category_name": "Data",
                "subcategory_name": "Databases",
                **body,
            },
        )

    def node(name):
        row = IsmsOrgNode(name=name, node_type="role")
        keen.db.add(row)
        keen.db.commit()
        return str(row.id)

    return SimpleNamespace(
        keen=keen,
        manager=manager,
        reader=reader,
        call=call,
        manage=manage,
        category=category,
        categories=categories,
        asset=asset,
        node=node,
    )


def names(items, key="name"):
    return [item[key] for item in items]


# --- Categories and subcategories -------------------------------------------------


def test_a_new_category_starts_with_a_general_subcategory(reg):
    made = reg.manage("POST", "asset-categories", {"name": "  Hardware  "})
    sub = reg.manage(
        "POST", "asset-subcategories", {"name": " Laptops ", "category_id": made["id"]}
    )

    assert made == {"id": made["id"], "name": "Hardware"}
    assert sub == {"id": sub["id"], "name": "Laptops", "category_id": made["id"]}
    (listed,) = reg.categories()
    assert (listed["id"], listed["name"]) == (made["id"], "Hardware")
    assert names(listed["subcategories"]) == ["General", "Laptops"]


def test_category_and_subcategory_names_are_unique_ignoring_case(reg):
    hardware = reg.category("Hardware", "Laptops")
    software = reg.category("Software")

    refusals = [
        ("asset-categories", {"name": "HARDWARE"}, "Asset category already exists"),
        ("asset-categories", {"name": "   "}, "name is required"),
        (
            "asset-subcategories",
            {"name": "laptops", "category_id": hardware["id"]},
            "Asset subcategory already exists",
        ),
        (
            "asset-subcategories",
            {"name": "  ", "category_id": hardware["id"]},
            "name is required",
        ),
        (
            "asset-subcategories",
            {"name": "Laptops", "category_id": str(uuid.uuid4())},
            "Unknown asset category",
        ),
    ]
    for path, body, detail in refusals:
        assert reg.manage("POST", path, body, 400)["detail"] == detail, body

    # The same subcategory name may sit in another category.
    reg.manage(
        "POST",
        "asset-subcategories",
        {"name": "Laptops", "category_id": software["id"]},
    )
    assert [(c["name"], names(c["subcategories"])) for c in reg.categories()] == [
        ("Hardware", ["General", "Laptops"]),
        ("Software", ["General", "Laptops"]),
    ]


# --- Licences ---------------------------------------------------------------------


def test_licences_are_unique_searchable_and_count_their_assets(reg):
    seat = reg.manage(
        "POST", "licenses", {"name": " Enterprise seat ", "description": "Per user"}
    )
    reg.manage("POST", "licenses", {"name": "Open source", "description": "MIT"})
    reg.asset(license_id=seat["id"])

    assert (seat["name"], seat["description"], seat["asset_count"]) == (
        "Enterprise seat",
        "Per user",
        0,
    )
    duplicate = reg.manage("POST", "licenses", {"name": "ENTERPRISE SEAT"}, 400)
    assert duplicate["detail"] == "License already exists"
    listing = reg.call(reg.reader, "GET", "licenses")
    assert [(i["name"], i["asset_count"]) for i in listing["items"]] == [
        ("Enterprise seat", 1),
        ("Open source", 0),
    ]
    found = reg.call(reg.reader, "GET", "licenses", params={"q": "mit"})
    assert (found["total"], names(found["items"])) == (1, ["Open source"])


def test_renaming_a_licence_renames_it_on_its_assets(reg):
    seat = reg.manage("POST", "licenses", {"name": "Enterprise seat"})
    other = reg.manage("POST", "licenses", {"name": "Open source"})
    held = reg.asset(license_id=seat["id"])

    clash = reg.manage("PATCH", f"licenses/{seat['id']}", {"name": "open SOURCE"}, 400)
    renamed = reg.manage(
        "PATCH",
        f"licenses/{seat['id']}",
        {"name": "Site licence", "description": "All"},
    )

    assert clash["detail"] == "License already exists"
    assert (renamed["name"], renamed["description"]) == ("Site licence", "All")
    asset = reg.call(reg.reader, "GET", f"assets/{held['id']}")
    assert (asset["license_id"], asset["license"], asset["license_name"]) == (
        seat["id"],
        "Site licence",
        "Site licence",
    )
    # The stored text copy follows the rename for searches.
    reg.keen.db.expire_all()
    assert reg.keen.db.get(RiskAsset, uuid.UUID(held["id"])).license == "Site licence"
    found = reg.call(reg.reader, "GET", "assets", params={"q": "site lic"})
    assert names(found["items"]) == ["Payroll database"]
    found = reg.call(reg.reader, "GET", "licenses", params={"q": "open"})
    assert [item["id"] for item in found["items"]] == [other["id"]]


def test_a_licence_in_use_cannot_be_deleted(reg):
    seat = reg.manage("POST", "licenses", {"name": "Enterprise seat"})
    held = reg.asset(license_id=seat["id"])

    refused = reg.manage("DELETE", f"licenses/{seat['id']}", status=400)
    assert refused["detail"] == (
        "Cannot delete a license that is related to one or more assets"
    )
    reg.manage("PATCH", f"assets/{held['id']}", {"license_id": None})
    assert reg.manage("DELETE", f"licenses/{seat['id']}") == {"ok": True}

    assert reg.call(reg.reader, "GET", "licenses")["items"] == []
    missing = reg.manage("DELETE", f"licenses/{seat['id']}", status=404)
    assert missing["detail"] == "License not found"
    malformed = reg.manage("PATCH", "licenses/seat", {"name": "x"}, 400)
    assert malformed["detail"] == "license_id must be a UUID"


# --- Assets -------------------------------------------------------------------------


def test_an_asset_records_its_place_licence_and_owners(reg):
    owner, custodian = reg.node("Database owner"), reg.node("Register custodian")
    data = reg.category("Data", "Databases")
    databases = next(s for s in data["subcategories"] if s["name"] == "Databases")
    seat = reg.manage("POST", "licenses", {"name": "Enterprise seat"})

    created = reg.manage(
        "POST",
        "assets",
        {
            "asset": "  Payroll database  ",
            "category_id": data["id"],
            "subcategory_id": databases["id"],
            "license_id": seat["id"],
            "owner_org_node_id": owner,
            "register_held_by_org_node_id": custodian,
            "description": "  Salaries  ",
        },
    )

    assert {key: created[key] for key in ASSET_FIELDS} == {
        "asset": "Payroll database",
        "name": "Payroll database",
        "category_id": data["id"],
        "category_name": "Data",
        "subcategory_id": databases["id"],
        "subcategory_name": "Databases",
        "license_id": seat["id"],
        "license": "Enterprise seat",
        "owner_org_node_id": owner,
        "register_held_by_org_node_id": custodian,
        "description": "Salaries",
        "risk_count": 0,
    }
    assert created["owner_org_node"]["name"] == "Database owner"
    assert reg.call(reg.reader, "GET", f"assets/{created['id']}") == created


ASSET_FIELDS = (
    "asset",
    "name",
    "category_id",
    "category_name",
    "subcategory_id",
    "subcategory_name",
    "license_id",
    "license",
    "owner_org_node_id",
    "register_held_by_org_node_id",
    "description",
    "risk_count",
)


def test_named_categories_and_licences_are_reused_ignoring_case(reg):
    first = reg.asset(license_name="Enterprise seat")
    second = reg.manage(
        "POST",
        "assets",
        {
            "asset": "HR database",
            "category_name": "DATA",
            "subcategory_name": "databases",
            "license_name": "enterprise SEAT",
        },
    )

    assert (second["category_id"], second["subcategory_id"], second["license_id"]) == (
        first["category_id"],
        first["subcategory_id"],
        first["license_id"],
    )
    assert names(reg.call(reg.reader, "GET", "licenses")["items"]) == [
        "Enterprise seat"
    ]
    assert [(c["name"], names(c["subcategories"])) for c in reg.categories()] == [
        ("Data", ["Databases"])
    ]


def test_an_asset_name_is_unique_within_its_subcategory(reg):
    original = reg.asset()
    elsewhere = reg.asset(subcategory_name="Spreadsheets")

    refused = reg.manage(
        "POST",
        "assets",
        {
            "asset": "PAYROLL database",
            "category_name": "Data",
            "subcategory_name": "Databases",
        },
        400,
    )
    moved = reg.manage(
        "PATCH",
        f"assets/{elsewhere['id']}",
        {"subcategory_id": original["subcategory_id"]},
        400,
    )

    assert (refused["detail"], moved["detail"]) == (
        "Asset already exists",
        "Asset already exists",
    )
    listing = reg.call(reg.reader, "GET", "assets")
    assert [(i["name"], i["subcategory_name"]) for i in listing["items"]] == [
        ("Payroll database", "Databases"),
        ("Payroll database", "Spreadsheets"),
    ]


@pytest.mark.parametrize(
    "change,status,detail",
    [
        ({"asset": "   "}, 400, "asset is required"),
        (
            {"category_id": "00000000-0000-0000-0000-000000000000"},
            400,
            "Unknown asset category",
        ),
        ({"category_name": "  "}, 400, "category is required"),
        ({"subcategory_name": "  "}, 400, "subcategory is required"),
        (
            {"license_id": "00000000-0000-0000-0000-000000000000"},
            404,
            "License not found",
        ),
        (
            {"owner_org_node_id": "00000000-0000-0000-0000-000000000000"},
            400,
            "Unknown organisation chart node",
        ),
        (
            {"register_held_by_org_node_id": "00000000-0000-0000-0000-000000000000"},
            400,
            "Unknown organisation chart node",
        ),
    ],
    ids=[
        "no-name",
        "unknown-category",
        "blank-category",
        "blank-subcategory",
        "unknown-licence",
        "unknown-owner",
        "unknown-custodian",
    ],
)
def test_invalid_assets_are_refused_on_create_and_update(reg, change, status, detail):
    original = reg.asset()

    created = reg.manage(
        "POST",
        "assets",
        {
            "asset": "Kiosk",
            "category_name": "Hardware",
            "subcategory_name": "Devices",
            **change,
        },
        status,
    )
    updated = reg.manage("PATCH", f"assets/{original['id']}", change, status)

    assert (created["detail"], updated["detail"]) == (detail, detail)
    assert reg.call(reg.reader, "GET", "assets")["items"] == [original]


def test_moving_an_asset_to_another_category_needs_a_subcategory_there(reg):
    original = reg.asset()
    hardware = reg.category("Hardware", "Devices")
    devices = next(s for s in hardware["subcategories"] if s["name"] == "Devices")

    # The asset's subcategory belongs to its old category.
    stranded = reg.manage(
        "PATCH", f"assets/{original['id']}", {"category_id": hardware["id"]}, 400
    )
    foreign = reg.manage(
        "PATCH",
        f"assets/{original['id']}",
        {"category_id": hardware["id"], "subcategory_id": original["subcategory_id"]},
        400,
    )
    moved = reg.manage(
        "PATCH",
        f"assets/{original['id']}",
        {"category_id": hardware["id"], "subcategory_id": devices["id"]},
    )

    assert stranded["detail"] == "subcategory is required"
    assert foreign["detail"] == "Unknown asset subcategory"
    assert (moved["category_name"], moved["subcategory_name"]) == (
        "Hardware",
        "Devices",
    )
    # Naming the category keeps a same-named subcategory in the new category.
    back = reg.manage(
        "PATCH", f"assets/{original['id']}", {"category_name": "data"}, 400
    )
    assert back["detail"] == "subcategory is required"


def test_an_update_changes_only_the_fields_it_names(reg):
    owner = reg.node("Database owner")
    original = reg.asset(
        license_name="Enterprise seat", owner_org_node_id=owner, description="Salaries"
    )
    path = f"assets/{original['id']}"

    renamed = reg.manage("PATCH", path, {"asset": "Payroll DB"})
    cleared = reg.manage(
        "PATCH",
        path,
        {"license_id": None, "owner_org_node_id": None, "description": ""},
    )

    assert renamed == {
        **original,
        "asset": "Payroll DB",
        "name": "Payroll DB",
        "display": renamed["display"],
        "updated_at": renamed["updated_at"],
    }
    assert {
        key: cleared[key] for key in ("license_id", "license", "owner_org_node_id")
    } == {
        "license_id": None,
        "license": "",
        "owner_org_node_id": None,
    }
    assert (cleared["name"], cleared["description"], cleared["category_id"]) == (
        "Payroll DB",
        "",
        original["category_id"],
    )


def test_the_register_searches_names_licences_and_places(reg):
    reg.asset(license_name="Enterprise seat", description="Salaries")
    reg.asset("Kiosk", category_name="Hardware", subcategory_name="Devices")
    reg.asset("Ledger", subcategory_name="Spreadsheets")
    data = next(c for c in reg.categories() if c["name"] == "Data")
    sheets = next(s for s in data["subcategories"] if s["name"] == "Spreadsheets")

    def listed(**params):
        body = reg.call(reg.reader, "GET", "assets", params=params)
        return body["total"], names(body["items"])

    assert listed() == (3, ["Kiosk", "Ledger", "Payroll database"])
    assert listed(q="ENTERPRISE") == (1, ["Payroll database"])
    assert listed(q="salar") == (1, ["Payroll database"])
    assert listed(q="hardware") == (1, ["Kiosk"])
    assert listed(q="spreadsheet") == (1, ["Ledger"])
    assert listed(category_id=data["id"]) == (2, ["Ledger", "Payroll database"])
    assert listed(subcategory_id=sheets["id"]) == (1, ["Ledger"])
    assert listed(limit=1, offset=1) == (3, ["Ledger"])
    for param in ("category_id", "subcategory_id"):
        refused = reg.call(reg.reader, "GET", "assets", 400, params={param: "data"})
        assert refused["detail"] == f"{param} must be a UUID"


def test_an_asset_with_risks_cannot_be_deleted(reg):
    held = reg.asset()
    free = reg.asset("Kiosk")
    reg.keen.db.add(Risk(asset_id=uuid.UUID(held["id"]), threat_summary="Export"))
    reg.keen.db.commit()

    refused = reg.manage("DELETE", f"assets/{held['id']}", status=400)
    deleted = reg.manage("DELETE", f"assets/{free['id']}")

    assert refused["detail"] == "Cannot delete an asset referenced by CIA Triad risks"
    assert deleted == {"ok": True}
    assert names(reg.call(reg.reader, "GET", "assets")["items"]) == ["Payroll database"]
    assert reg.call(reg.reader, "GET", f"assets/{held['id']}")["risk_count"] == 1
    missing = reg.call(reg.reader, "GET", f"assets/{free['id']}", 404)
    assert missing["detail"] == "Asset not found"


def test_reading_the_register_needs_isms_read_and_changes_need_isms_manage(reg):
    held = reg.asset(license_name="Enterprise seat")
    licence = held["license_id"]
    outsider = reg.keen.client(reg.keen.user("events.read"))

    for path in ("asset-categories", "licenses", "assets", f"assets/{held['id']}"):
        assert reg.reader.get("/v1/isms/" + path).status_code == 200
        refused = outsider.get("/v1/isms/" + path)
        assert refused.status_code == 403
    changes = [
        ("POST", "asset-categories", {"name": "Hardware"}),
        ("POST", "asset-subcategories", {"name": "x", "category_name": "Data"}),
        ("POST", "licenses", {"name": "Open source"}),
        ("PATCH", f"licenses/{licence}", {"name": "Renamed"}),
        ("DELETE", f"licenses/{licence}", None),
        (
            "POST",
            "assets",
            {
                "asset": "Kiosk",
                "category_name": "Data",
                "subcategory_name": "Databases",
            },
        ),
        ("PATCH", f"assets/{held['id']}", {"description": "Changed"}),
        ("DELETE", f"assets/{held['id']}", None),
    ]
    for method, path, body in changes:
        refused = reg.reader.request(
            method,
            "/v1/isms/" + path,
            headers=unsafe(reg.reader),
            **({"json": body} if body is not None else {}),
        )
        assert (refused.status_code, refused.json()["detail"]) == (403, MANAGE), path

    assert reg.call(reg.reader, "GET", f"assets/{held['id']}") == held
    assert len(reg.categories()) == 1
    assert names(reg.call(reg.reader, "GET", "licenses")["items"]) == [
        "Enterprise seat"
    ]


def test_the_risk_register_refuses_unknown_organisation_chart_nodes_too(reg):
    risks = reg.keen.client(reg.keen.user("risk.manage"))
    held = reg.asset()
    unknown = str(uuid.uuid4())
    body = {"category_name": "Data", "subcategory_name": "Databases"}

    created = risks.post(
        "/v1/risks/assets",
        json={**body, "name": "Kiosk", "owner_org_node_id": unknown},
        headers=unsafe(risks),
    )
    updated = risks.patch(
        f"/v1/risks/assets/{held['id']}",
        json={**body, "name": held["name"], "register_held_by_org_node_id": unknown},
        headers=unsafe(risks),
    )

    for refused in (created, updated):
        assert (refused.status_code, refused.json()["detail"]) == (
            400,
            "Unknown organisation chart node",
        )
    assert reg.call(reg.reader, "GET", "assets")["items"] == [held]
