"""Disabled KEEN-AF never gains implicit template links, even from stale forms."""
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes import risks, risk_register
from app.core.config import settings
from app.db.models import (User, Framework, ControlItem, ManagedConfiguration, RiskLibraryEntry,
                           RiskControlLink, RiskAsset, RiskCategory, RiskAssetSubcategory)
from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, 'enabled_frameworks', '')
    engine = create_engine('sqlite://')
    create_sqlite_schema(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize('enabled,environment', [(True,''), (False,''), (True,'CUSTOM')])
def test_library_stays_available_but_implicit_links_require_enabled_framework(db, monkeypatch, enabled, environment):
    monkeypatch.setattr(settings, 'enabled_frameworks', environment)
    user = User(username='admin', password_hash='unused', role='admin')
    category = RiskCategory(name='IT')
    template = RiskLibraryEntry(name='Network exposure', threat_summary='Example risk',
                               suggested_assessment={'keen_af_control_refs': ['NET-1', 'REMOVED']})
    db.add_all([user, category, template, Framework(slug='CUSTOM'), Framework(slug='KEEN-AF:1.0'),
                ControlItem(framework_slug='CUSTOM', ref='C1', type='custom', title='Network protection'),
                ControlItem(framework_slug='KEEN-AF:1.0', ref='NET-1', type='custom', title='Network protection'),
                ManagedConfiguration(name='organisation-frameworks', version=1,
                    document={'enabled': ['CUSTOM', 'KEEN-AF:1.0'] if enabled else ['CUSTOM'], 'default': 'CUSTOM'})])
    db.flush()
    sub = RiskAssetSubcategory(name='Servers', category_id=category.id); db.add(sub); db.flush()
    asset = RiskAsset(name='Router', category_id=category.id, subcategory_id=sub.id); db.add(asset); db.commit()
    assert risk_register.list_library(user, db)['items'][0]['name'] == 'Network exposure'
    payload = risks.RiskUpsertPayload(asset_id=asset.id, threat_summary='Network exposure',
                                     risk_types=['Confidentiality'], framework='CUSTOM', controls=['C1'], library_template_id=template.id)
    saved = risks.create_risk(payload, Request({'type':'http','method':'POST','path':'/api/v1/risks'}), user, db)
    links = {(link.framework_slug, link.control.ref) for link in db.query(RiskControlLink).filter_by(risk_id=uuid.UUID(saved['id'])).all()}
    expected = {('CUSTOM','C1')}
    if enabled and not environment: expected.add(('KEEN-AF:1.0','NET-1'))
    assert links == expected
