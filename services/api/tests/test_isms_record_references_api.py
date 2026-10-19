"""People, accounts, roles and documents that ISMS records refer to.

Meetings, organisation roles, objectives and access-matrix entries name users,
people, hosting accounts, roles and documents by ID. The API accepts only
records that exist (and users who are active), lists each one once, and
replaces a list only when an edit sends it.
"""

import uuid

import pytest

# Imported fixtures: the assembled application and a populated ISMS.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_isms_overview_api_workflows import isms  # noqa: F401

MEETINGS = "/v1/isms/meetings"


def _meeting(isms, status=200, **fields):  # noqa: F811
    body = {"title": "Risk review", "date": "2026-04-01", **fields}
    return isms.manage("POST", MEETINGS, body, status=status)


def test_meeting_people_are_listed_once_and_replaced_only_when_sent(isms):  # noqa: F811
    person = isms.manage(
        "POST",
        "/v1/isms/people",
        {"name": "External auditor", "email": "auditor@example.test"},
        status=201,
    )
    member, manager = str(isms.member.id), str(isms.manager.id)

    meeting = _meeting(
        isms,
        attendee_user_ids=[member, member],
        apology_user_ids=[manager],
        attendee_person_ids=[person["id"], person["id"]],
        links=[
            {"link_type": "isms_document", "document_id": isms.document["id"]},
            {"title": "Slides", "url": "https://files.example/slides.pdf"},
        ],
    )

    assert meeting["attendee_user_ids"] == [member]
    assert meeting["apology_user_ids"] == [manager]
    assert meeting["attendee_person_ids"] == [person["id"]]
    assert [a.get("name") for a in meeting["attendees"]].count("External auditor") == 1
    assert [
        (link["link_type"], link["title"], link["url"]) for link in meeting["links"]
    ] == [
        ("isms_document", "Security policy", None),
        ("external_url", "Slides", "https://files.example/slides.pdf"),
    ]

    path = f"{MEETINGS}/{meeting['id']}"
    retitled = isms.manage("PATCH", path, {"title": "Quarterly risk review"})
    assert retitled["attendee_user_ids"] == [member]
    assert retitled["attendee_person_ids"] == [person["id"]]
    assert len(retitled["links"]) == 2

    emptied = isms.manage(
        "PATCH", path, {"attendee_user_ids": [], "apology_person_ids": [], "links": []}
    )
    assert (emptied["attendee_user_ids"], emptied["apology_user_ids"]) == ([], [])
    assert (emptied["attendee_person_ids"], emptied["links"]) == ([], [])


@pytest.mark.parametrize(
    "fields,status,detail",
    [
        ({"date": None}, 400, "date is required"),
        ({"attendee_user_ids": ["retired"]}, 400, "Unknown user: "),
        ({"apology_user_ids": [str(uuid.UUID(int=7))]}, 400, "Unknown user: "),
        (
            {"attendee_person_ids": [str(uuid.UUID(int=8))]},
            400,
            "Unknown Person selected for meeting",
        ),
        (
            {
                "links": [
                    {"link_type": "isms_document", "document_id": str(uuid.UUID(int=9))}
                ]
            },
            400,
            "Unknown ISMS document",
        ),
        ({"links": [{"link_type": "isms_document"}]}, 400, "Unknown ISMS document"),
        (
            {"links": [{"title": "No address"}]},
            400,
            "url is required for external links",
        ),
        (
            {"links": [{"link_type": "ftp_share", "url": "https://files.example/a"}]},
            400,
            "Invalid meeting link_type",
        ),
    ],
    ids=[
        "no-date",
        "inactive-user",
        "unknown-user",
        "unknown-person",
        "unknown-document",
        "no-document",
        "no-url",
        "link-type",
    ],
)
def test_meetings_naming_missing_records_are_refused(isms, fields, status, detail):  # noqa: F811
    if fields.get("attendee_user_ids") == ["retired"]:
        fields = {"attendee_user_ids": [str(isms.retired.id)]}
    before = len(isms.manage("GET", MEETINGS)["items"])

    refused = _meeting(isms, status=status, **fields)

    assert refused["detail"].startswith(detail)
    assert len(isms.manage("GET", MEETINGS)["items"]) == before


def test_meeting_links_need_web_addresses(isms):  # noqa: F811
    refused = _meeting(
        isms, status=422, links=[{"title": "Script", "url": "javascript:alert(1)"}]
    )

    assert refused["detail"]


def test_role_members_and_objective_resources_must_be_active_users(isms):  # noqa: F811
    member = str(isms.member.id)
    role = isms.manage(
        "POST",
        "/v1/isms/org-nodes",
        {"name": "Auditors", "node_type": "team", "user_ids": [member, member]},
    )
    assert [(p["user"]["id"], p["relationship_type"]) for p in role["people"]] == [
        (member, "member")
    ]
    for path, field in (
        ("/v1/isms/org-nodes", "user_ids"),
        ("/v1/isms/objectives", "resource_user_ids"),
    ):
        refused = isms.manage(
            "POST",
            path,
            {
                "name": "Team",
                "requirement": "R",
                "goal": "G",
                field: [str(isms.retired.id)],
            },
            status=400,
        )
        assert refused["detail"] == f"Unknown user: {isms.retired.id}"

    objective = isms.manage(
        "PATCH",
        f"/v1/isms/objectives/{isms.other_objective['id']}",
        {"resource_user_ids": [member, member]},
    )
    assert objective["resource_user_ids"] == [member]


def test_access_entries_name_existing_accounts_and_roles(isms):  # noqa: F811
    path = f"/v1/isms/access-control-matrix/{isms.entry['id']}"
    account = isms.entry["aws_account_ids"][0]
    role = isms.role["id"]

    kept = isms.manage(
        "PATCH",
        path,
        {"aws_account_ids": [account, account], "role_org_node_ids": [role, role]},
    )

    assert (kept["aws_account_ids"], kept["role_org_node_ids"]) == ([account], [role])
    missing_account = str(uuid.UUID(int=11))
    missing_role = str(uuid.UUID(int=12))
    refused = isms.manage(
        "PATCH", path, {"aws_account_ids": [missing_account]}, status=400
    )
    assert refused["detail"] == f"Unknown hosting account: {missing_account}"
    refused = isms.manage(
        "PATCH", path, {"role_org_node_ids": [missing_role]}, status=400
    )
    assert refused["detail"] == "Unknown organisation chart role"
    unchanged = isms.manage("GET", path)
    assert (unchanged["aws_account_ids"], unchanged["role_org_node_ids"]) == (
        [account],
        [role],
    )
