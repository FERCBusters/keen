"""Disabled KEEN-AF never gains implicit template links, even from stale forms."""

import uuid

import pytest
from app.api.routes import risk_register, risks
from app.core.config import settings
from app.db.models import (
    Artifact,
    ControlItem,
    Event,
    Framework,
    ManagedConfiguration,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    RiskLibraryEntry,
    User,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize(
    "enabled,environment", [(True, ""), (False, ""), (True, "CUSTOM")]
)
def test_library_stays_available_but_implicit_links_require_enabled_framework(
    db, monkeypatch, tmp_path, enabled, environment
):
    monkeypatch.setattr(settings, "enabled_frameworks", environment)
    # Risk control links record an automatic diary entry with artifacts.
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path))
    user = User(username="admin", password_hash="unused", role="admin")
    category = RiskCategory(name="IT")
    template = RiskLibraryEntry(
        name="Network exposure",
        threat_summary="Example risk",
        suggested_assessment={"keen_af_control_refs": ["NET-1", "REMOVED"]},
    )
    db.add_all(
        [
            user,
            category,
            template,
            Framework(slug="CUSTOM"),
            Framework(slug="KEEN-AF:1.0"),
            ControlItem(
                framework_slug="CUSTOM",
                ref="C1",
                type="custom",
                title="Network protection",
            ),
            ControlItem(
                framework_slug="KEEN-AF:1.0",
                ref="NET-1",
                type="custom",
                title="Network protection",
            ),
            ManagedConfiguration(
                name="organisation-frameworks",
                version=1,
                document={
                    "enabled": ["CUSTOM", "KEEN-AF:1.0"] if enabled else ["CUSTOM"],
                    "default": "CUSTOM",
                },
            ),
        ]
    )
    db.flush()
    sub = RiskAssetSubcategory(name="Servers", category_id=category.id)
    db.add(sub)
    db.flush()
    asset = RiskAsset(name="Router", category_id=category.id, subcategory_id=sub.id)
    db.add(asset)
    db.commit()
    assert (
        risk_register.list_library(user, db)["items"][0]["name"] == "Network exposure"
    )
    payload = risks.RiskUpsertPayload(
        asset_id=asset.id,
        threat_summary="Network exposure",
        risk_types=["Confidentiality"],
        framework="CUSTOM",
        controls=["C1"],
        library_template_id=template.id,
    )
    saved = risks.create_risk(
        payload,
        Request(
            {"type": "http", "method": "POST", "path": "/api/v1/risks", "headers": []}
        ),
        user,
        db,
    )
    links = {
        (link.framework_slug, link.control.ref)
        for link in db.query(RiskControlLink)
        .filter_by(risk_id=uuid.UUID(saved["id"]))
        .all()
    }
    expected = {("CUSTOM", "C1")}
    if enabled and not environment:
        expected.add(("KEEN-AF:1.0", "NET-1"))
    assert links == expected
    # One automatic diary entry records every framework's links at creation.
    entry = (
        db.query(Event)
        .filter_by(source="diary", external_id=f"isms-link:risk:{saved['id']}")
        .one()
    )
    assert set(entry.normalized_payload["details"]["linked"]) == {
        framework for framework, _ref in expected
    }
    assert (
        not db.query(Artifact)
        .filter_by(event_id=entry.id, kind="diary_entry_edit")
        .count()
    )
