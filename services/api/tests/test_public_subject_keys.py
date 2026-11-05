"""The public subject key that KEEN issues to agents configured for public
subjects: created once, stored encrypted, and refused when unreadable."""

import pytest
from app.db.models import UeuSettings
from app.integrations.transport import decrypt, encrypt
from app.security.agent_signatures import b64url, unb64url
from app.services.errors import DomainError
from app.services.ueu_subjects import public_subject_key, subject_grant
from cryptography.fernet import Fernet

UNREADABLE = (
    "The public subject key cannot be read with the configured "
    "KEEN_INTEGRATION_SECRET_KEY"
)


@pytest.fixture
def db(contract_db, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    return contract_db


def test_an_empty_settings_row_receives_the_new_key(db):
    db.add(UeuSettings(id=1, public_subject_key=None))
    db.flush()

    key = public_subject_key(db)

    assert len(unb64url(key)) == 32
    db.expire_all()
    (row,) = db.query(UeuSettings).all()
    assert decrypt(row.public_subject_key) == {"key": key}
    # Later agents receive the same key.
    assert subject_grant(db, "public") == {"type": "public", "key": key}


@pytest.mark.parametrize(
    "stored",
    [
        lambda: encrypt({"key": b64url(b"too short")}),
        lambda: encrypt({"other": "field"}),
        lambda: Fernet(Fernet.generate_key()).encrypt(b'{"key": "x"}').decode(),
    ],
    ids=["wrong-length", "no-key", "other-secret"],
)
def test_an_unreadable_public_subject_key_is_refused_and_kept(db, stored):
    sealed = stored()
    db.add(UeuSettings(id=1, public_subject_key=sealed))
    db.flush()

    with pytest.raises(DomainError) as refused:
        public_subject_key(db)

    assert (refused.value.status_code, refused.value.detail) == (503, UNREADABLE)
    # The stored key is left for an operator to recover, never replaced.
    db.expire_all()
    assert db.get(UeuSettings, 1).public_subject_key == sealed


def test_pairwise_subjects_need_no_key(db, monkeypatch):
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")
    assert subject_grant(db, "pairwise") == {"type": "pairwise"}
    assert db.get(UeuSettings, 1) is None
