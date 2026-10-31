"""Retention selectors and server-side identity validation."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import retention_management as service
from app.services.errors import DomainError
from sqlalchemy import text


@pytest.fixture
def choices_db(contract_db, monkeypatch):
    from app.core import managed_configuration
    from app.services import managed_configurations

    monkeypatch.setattr(
        managed_configuration,
        "load_document",
        lambda *a, **k: {"providers": {"configured": {}}},
    )
    monkeypatch.setattr(
        managed_configurations,
        "_normalized_rules",
        lambda db: [
            {"id": "existing", "description": "Existing rule"},
            {"id": "disabled-rule", "enabled": False},
        ],
    )
    return contract_db


def test_choices_use_exact_ids_and_include_disabled_rules(choices_db):
    choices = service.override_choices(choices_db)
    assert {item["key"] for item in choices["rule"]} == {"existing", "disabled-rule"}
    sources = {item["key"] for item in choices["source"]}
    assert {"github", "keen-agent", "diary", "webhook:configured"} <= sources
    assert "webhooks" not in sources


def test_preview_and_save_reject_unknown_identifiers_before_mutating(
    choices_db, monkeypatch
):
    preview = Mock()
    monkeypatch.setattr(service.retention, "preview", preview)
    monkeypatch.setattr(service.retention, "lock", lambda db: None)
    for scope, key in [("source", "githbu"), ("rule", "typo")]:
        payload = service.SettingsPayload(
            overrides=[dict(scope=scope, key=key, mode="count", value=5)]
        )
        for operation in (service.preview, service.set_policy):
            with pytest.raises(DomainError) as caught:
                operation(
                    payload=payload,
                    db=choices_db,
                    **(
                        {"user": SimpleNamespace(username="admin")}
                        if operation is service.set_policy
                        else {}
                    ),
                )
            assert caught.value.status_code == 422
    preview.assert_not_called()
    assert (
        choices_db.execute(
            text("SELECT count(*) FROM evidence_retention_overrides")
        ).scalar()
        == 0
    )


def test_existing_and_saved_unavailable_values_are_accepted(choices_db):
    choices_db.execute(
        text(
            "INSERT INTO evidence_retention_overrides(scope,key,mode) VALUES ('rule','deleted-rule','disabled')"
        )
    )
    for scope, key in [
        ("source", "github"),
        ("rule", "existing"),
        ("rule", "deleted-rule"),
    ]:
        service.validate_override_choices(
            service.SettingsPayload(
                overrides=[dict(scope=scope, key=key, mode="disabled")]
            ),
            choices_db,
        )
