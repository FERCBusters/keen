"""Hosted workspace owner bootstrap: one OIDC owner, never a takeover.

`python -m app.bootstrap_hosted` runs after migrations in hosted mode. It may
create the first owner of an empty installation and confirm that owner on
later runs; it must never claim an existing installation or quietly restore
an owner an administrator has changed.
"""

import pytest
from app import bootstrap_hosted
from app.core.config import settings
from app.db.models import User, UserIdentity
from app.security.passwords import hash_password, verify_password
from sqlalchemy.orm import sessionmaker

ISSUER = "https://idp.example"
SUBJECT = "owner-subject"
EMAIL = "owner@example.com"


@pytest.fixture
def db(contract_db):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("The bootstrap serialises runs with a PostgreSQL advisory lock")
    assert contract_db.query(User).count() == 0
    return contract_db


def _bootstrap(db, *, issuer=ISSUER, subject=SUBJECT, email=EMAIL):
    return bootstrap_hosted.bootstrap(db, issuer=issuer, subject=subject, email=email)


def test_the_first_run_creates_one_oidc_owner_and_later_runs_confirm_it(db):
    owner = _bootstrap(db)

    assert (owner.username, owner.email, owner.role, owner.is_active) == (
        "workspace-owner",
        EMAIL,
        "admin",
        True,
    )
    (identity,) = db.query(UserIdentity).all()
    assert (identity.user_id, identity.provider, identity.issuer, identity.subject) == (
        owner.id,
        "oidc",
        ISSUER,
        SUBJECT,
    )
    # The owner's local password is random, so it opens nothing.
    for guess in ("", "workspace-owner", EMAIL, SUBJECT):
        assert not verify_password(guess, owner.password_hash)

    assert _bootstrap(db).id == owner.id
    assert (db.query(User).count(), db.query(UserIdentity).count()) == (1, 1)


@pytest.mark.parametrize(
    "subject", [SUBJECT, "another-subject"], ids=["same-owner", "another-owner"]
)
def test_an_existing_installation_is_never_claimed(db, subject):
    db.add(
        User(
            username="admin",
            password_hash=hash_password("synthetic-password"),
            role="admin",
            is_active=True,
        )
    )
    db.flush()

    with pytest.raises(RuntimeError, match="Refusing to claim an existing KEEN"):
        _bootstrap(db, subject=subject)

    assert db.query(UserIdentity).count() == 0
    assert [u.username for u in db.query(User)] == ["admin"]


@pytest.mark.parametrize(
    "issuer,subject",
    [(ISSUER, "attacker-subject"), ("https://other-idp.example", SUBJECT)],
    ids=["other-subject", "other-issuer"],
)
def test_a_reconfigured_owner_identity_cannot_take_over(db, issuer, subject):
    _bootstrap(db)

    with pytest.raises(RuntimeError, match="Refusing to claim an existing KEEN"):
        _bootstrap(db, issuer=issuer, subject=subject)

    assert [(i.issuer, i.subject) for i in db.query(UserIdentity)] == [
        (ISSUER, SUBJECT)
    ]


@pytest.mark.parametrize(
    "change",
    [
        lambda owner, identity: setattr(owner, "is_active", False),
        lambda owner, identity: setattr(owner, "role", "normal"),
        lambda owner, identity: setattr(identity, "provider", "google"),
    ],
    ids=["deactivated", "demoted", "other-provider"],
)
def test_a_changed_owner_is_never_quietly_restored(db, change):
    owner = _bootstrap(db)
    identity = db.query(UserIdentity).one()
    change(owner, identity)
    db.flush()
    before = (owner.is_active, owner.role, identity.provider)

    with pytest.raises(RuntimeError, match="does not match a usable admin identity"):
        _bootstrap(db)

    db.refresh(owner)
    db.refresh(identity)
    assert (owner.is_active, owner.role, identity.provider) == before


@pytest.mark.parametrize(
    "issuer,subject,email",
    [
        ("http://idp.example", SUBJECT, EMAIL),
        ("idp.example", SUBJECT, EMAIL),
        (ISSUER, "", EMAIL),
        (ISSUER, SUBJECT, ""),
    ],
    ids=["plain-http-issuer", "bare-issuer", "no-subject", "no-email"],
)
def test_the_owner_identity_must_be_complete(db, issuer, subject, email):
    with pytest.raises(ValueError, match="Verified issuer, subject and email required"):
        _bootstrap(db, issuer=issuer, subject=subject, email=email)

    assert db.query(User).count() == 0


def test_the_command_refuses_outside_hosted_mode(monkeypatch):
    monkeypatch.setattr(settings, "hosted_mode", False)
    called = []
    monkeypatch.setattr(bootstrap_hosted, "bootstrap", lambda *a, **k: called.append(1))

    with pytest.raises(RuntimeError, match="Hosted mode is required"):
        bootstrap_hosted.main()
    assert called == []


def test_the_command_bootstraps_from_the_hosted_settings(db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "hosted_mode", True)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "hosted_owner_subject", SUBJECT)
    monkeypatch.setenv("KEEN_HOSTED_OWNER_EMAIL", EMAIL)
    monkeypatch.setattr(
        bootstrap_hosted,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), join_transaction_mode="create_savepoint"),
    )

    bootstrap_hosted.main()

    assert capsys.readouterr().out.strip() == "Hosted workspace owner is ready"
    (identity,) = db.query(UserIdentity).all()
    assert (identity.issuer, identity.subject, identity.email) == (
        ISSUER,
        SUBJECT,
        EMAIL,
    )
