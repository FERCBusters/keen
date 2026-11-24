"""The operator's account recovery script."""

from contextlib import nullcontext

import mfa_admin
import pytest
from app.core.config import settings
from app.db.models import UserIdentity, UserSsoEmail
from mosp.security.auth import create_user


@pytest.fixture
def planted(contract_db, monkeypatch):
    # An account that signs in only through SSO, holding only an identity
    # someone else linked after removing the owner's.
    monkeypatch.setattr(settings, "local_auth_enabled", False)
    monkeypatch.setattr(mfa_admin, "SessionLocal", lambda: nullcontext(contract_db))
    user = create_user(
        contract_db, username="ada", password="Synthetic-2b7e-passphrase", role="admin"
    )
    contract_db.add(
        UserIdentity(
            user_id=user.id,
            provider="oidc",
            issuer="https://idp.example",
            subject="planted-subject",
            email="planted@attacker.example",
            display_name="Someone",
            claims={},
        )
    )
    contract_db.add(UserSsoEmail(email="planted@attacker.example", user_id=user.id))
    contract_db.commit()
    return user


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["mfa_admin.py", *argv])
    return mfa_admin.main()


def test_the_listing_tells_identities_apart(planted, monkeypatch, capsys):
    run(monkeypatch, "ada", "--list-identities")
    line = capsys.readouterr().out.strip()
    assert "\toidc\thttps://idp.example planted-subject\tSomeone\t" in line
    assert "planted@attacker.example" in line


def test_an_operator_gives_the_owner_a_new_linking_address(
    planted, contract_db, monkeypatch, capsys
):
    reset = ("ada", "--reset", "--confirm-username", "ada")
    run(monkeypatch, *reset, "--link-address", "Ada@Example.org")
    assert "the address given is the account's only" in capsys.readouterr().out
    assert contract_db.query(UserIdentity).count() == 0
    assert [(a.email, a.user_id) for a in contract_db.query(UserSsoEmail)] == [
        ("ada@example.org", planted.id)
    ]


def test_an_identity_the_account_lacks_is_refused(planted, monkeypatch, capsys):
    reset = ("ada", "--reset", "--confirm-username", "ada")
    with pytest.raises(SystemExit):
        run(monkeypatch, *reset, "--keep-identity", "999")
    assert "no such SSO identity" in capsys.readouterr().err
