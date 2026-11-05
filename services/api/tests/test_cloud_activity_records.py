"""Cloud activity parsers keep a record whose status, result or principal ID
holds a list or an object.

Each parser reads such a field through ``text_field`` before using it as a
lookup key, so the value reads as empty and the record stays in the activity
collection with outcome "info". No network is used.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.ingest.cloud import aws, azure, digitalocean, linode, scaleway

from tests.cloud_fakes import ACCOUNT, trail_event

WHEN = datetime(2026, 10, 1, 9, 30)
STAMP = "2026-10-01T09:30:00Z"
SCOPE = "6a7b8c9d-0e1f-4a2b-9c3d-4e5f6a7b8c9d"
EVENT = "00000000-0000-4000-8000-000000000051"
APP = "00000000-0000-4000-8000-000000000005"


def _aws_sign_in(value):
    record = trail_event(
        "ConsoleLogin", WHEN.replace(tzinfo=timezone.utc), result=value
    )
    return aws.trail_entry(record, ACCOUNT)


def _azure_audit(value):
    return azure.audit_entry(
        {
            "id": "a1",
            "activityDateTime": STAMP,
            "activityDisplayName": "Add member to role",
            "result": value,
        },
        SCOPE,
    )


def _digitalocean_action(value):
    return digitalocean.action_entry(
        {"id": 7, "type": "power_on", "started_at": STAMP, "status": value}, SCOPE
    )


def _linode_login(value):
    return linode.login_entry(
        {"id": 1, "datetime": STAMP, "username": "alex", "status": value}, SCOPE
    )


def _linode_event(value):
    return linode.event_entry(
        {"id": 2, "created": STAMP, "action": "linode_boot", "status": value}, SCOPE
    )


def _scaleway_sign_in(value):
    return scaleway.sign_in_entry(
        {"id": EVENT, "recorded_at": STAMP, "result": value}, SCOPE
    )


def _scaleway_event(value):
    return scaleway.event_entry(
        {
            "id": EVENT,
            "recorded_at": STAMP,
            "method_name": "CreateServer",
            "principal": {"id": value},
        },
        SCOPE,
        {APP: "application/terraform"},
    )


# Each parser, a well-formed value for the field, and what that value reads as.
PARSERS = {
    "aws sign-in result": (_aws_sign_in, "Success", "outcome", "success"),
    "azure audit result": (_azure_audit, "success", "outcome", "success"),
    "digitalocean action status": (
        _digitalocean_action,
        "completed",
        "outcome",
        "success",
    ),
    "linode login status": (_linode_login, "successful", "outcome", "success"),
    "linode event status": (_linode_event, "finished", "outcome", "success"),
    "scaleway sign-in result": (_scaleway_sign_in, "success", "outcome", "success"),
    "scaleway event principal": (
        _scaleway_event,
        APP,
        "actor",
        "application/terraform",
    ),
}


@pytest.mark.parametrize(
    "value", [["success"], {"value": "success"}], ids=["list", "object"]
)
@pytest.mark.parametrize("parser", PARSERS)
def test_a_list_or_object_in_an_outcome_field_keeps_the_record(parser, value):
    parse, good, field, reading = PARSERS[parser]
    # The record is otherwise well formed: the usual value reads as expected.
    assert getattr(parse(good), field) == reading
    entry = parse(value)
    assert entry is not None
    assert (entry.at, entry.outcome) == (WHEN, "info")
    if field == "actor":
        assert entry.actor == ""
