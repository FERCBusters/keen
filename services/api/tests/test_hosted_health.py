"""Hosted readiness: the probe passes only when the workspace can serve its owner.

`python -m app.hosted_health` checks the database, the bootstrapped OIDC owner,
the broker and a worker. Each failure ends the probe with an error, so the
hosting platform keeps traffic away from an installation that cannot work.
"""

from types import SimpleNamespace

import pytest
from app import bootstrap_hosted, hosted_health
from app.core.config import settings
from app.db.models import User
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import sessionmaker

ISSUER = "https://idp.example"
SUBJECT = "owner-subject"


@pytest.fixture
def probe(contract_db, monkeypatch):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("The owner bootstrap holds a PostgreSQL advisory lock")
    for name, value in {
        "hosted_mode": True,
        "oidc_issuer": ISSUER,
        "hosted_owner_subject": SUBJECT,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(
        hosted_health,
        "SessionLocal",
        sessionmaker(
            bind=contract_db.get_bind(), join_transaction_mode="create_savepoint"
        ),
    )
    state = SimpleNamespace(pings=[], workers=[{"worker@keen": {"ok": "pong"}}])
    monkeypatch.setattr(
        hosted_health,
        "get_valkey",
        lambda: SimpleNamespace(ping=lambda: state.pings.append(True)),
    )
    from app.worker import celery_app as worker

    control = SimpleNamespace(ping=lambda timeout: state.workers)
    monkeypatch.setattr(worker, "celery_app", SimpleNamespace(control=control))
    state.db = contract_db
    return state


def _owner(db):
    return bootstrap_hosted.bootstrap(
        db, issuer=ISSUER, subject=SUBJECT, email="owner@example.com"
    )


def test_a_ready_workspace_passes_every_check(probe):
    _owner(probe.db)

    hosted_health.main()

    assert probe.pings == [True]


def test_the_probe_runs_only_in_hosted_mode(probe, monkeypatch):
    monkeypatch.setattr(settings, "hosted_mode", False)

    with pytest.raises(RuntimeError, match="Not a hosted installation"):
        hosted_health.main()
    assert probe.pings == []


def test_a_workspace_without_its_owner_is_not_ready(probe):
    with pytest.raises(NoResultFound):
        hosted_health.main()
    assert probe.pings == []


@pytest.mark.parametrize("change", ["inactive", "demoted"])
def test_an_owner_who_cannot_administer_the_workspace_fails_the_probe(probe, change):
    owner = _owner(probe.db)
    if change == "inactive":
        owner.is_active = False
    else:
        owner.role = "normal"
    probe.db.commit()

    with pytest.raises((NoResultFound, RuntimeError)) as failed:
        hosted_health.main()

    if change == "demoted":
        assert str(failed.value) == "Owner is not an administrator"
    assert probe.db.query(User).count() == 1


def test_a_workspace_without_a_responding_worker_is_not_ready(probe):
    _owner(probe.db)
    probe.workers = []

    with pytest.raises(RuntimeError, match="No worker responded"):
        hosted_health.main()
    assert probe.pings == [True]
