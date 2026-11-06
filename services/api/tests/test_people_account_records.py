"""The People record linked to a Keen account, made when an account first
needs one (for example, when a quiz is assigned to the account).

The concurrent case runs on separate PostgreSQL connections: another request
links the same account while this one is creating its record.
"""

import uuid

import pytest
from app.db.models import IsmsPerson, User
from app.services.people import ensure_person_for_user, person_for_user
from sqlalchemy import event as sql_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _account(**fields):
    return User(
        username=fields.pop("username", "person-" + uuid.uuid4().hex[:8]),
        password_hash="unused",
        role="normal",
        is_active=True,
        **fields,
    )


def test_no_account_has_no_people_record(contract_db):
    contract_db.add(IsmsPerson(name="Unlinked", user_id=None))
    contract_db.flush()

    assert person_for_user(contract_db, None) is None


def test_an_account_gains_one_record_named_after_it(contract_db):
    account = _account(username="ada@example.test")
    contract_db.add(account)
    contract_db.flush()

    made = ensure_person_for_user(contract_db, account)

    assert (made.name, made.email, made.user_id) == (
        "ada@example.test",
        "ada@example.test",
        account.id,
    )
    assert ensure_person_for_user(contract_db, account).id == made.id
    assert contract_db.query(IsmsPerson).filter_by(user_id=account.id).count() == 1


def test_a_record_for_an_account_that_no_longer_exists_is_refused(contract_db):
    # The account was removed after this request loaded it.
    gone = _account(id=uuid.uuid4())
    kept = IsmsPerson(name="Kept")
    contract_db.add(kept)
    contract_db.flush()

    with pytest.raises(
        IntegrityError,
        match='violates foreign key constraint "isms_people_user_id_fkey"',
    ):
        ensure_person_for_user(contract_db, gone)

    # Only the failed insert is undone; the caller's transaction carries on.
    assert contract_db.query(IsmsPerson).filter_by(user_id=gone.id).count() == 0
    assert contract_db.get(IsmsPerson, kept.id).name == "Kept"


def test_a_record_linked_concurrently_to_the_same_account_is_used(
    contract_postgres_engine,
):
    if contract_postgres_engine is None:
        pytest.skip("A concurrent link needs separate PostgreSQL connections")
    engine = contract_postgres_engine
    with Session(engine) as setup:
        account = _account()
        setup.add(account)
        setup.commit()
        account_id = account.id
    winner = {}

    def link_first(session, flush_context, instances):
        # Another request commits its link before this one writes.
        with Session(engine) as other:
            row = IsmsPerson(name="Linked elsewhere", user_id=account_id)
            other.add(row)
            other.commit()
            winner["id"] = row.id

    try:
        with Session(engine) as db:
            loaded = db.get(User, account_id)
            sql_event.listen(db, "before_flush", link_first, once=True)

            found = ensure_person_for_user(db, loaded)

            assert found.id == winner["id"]
            assert found.name == "Linked elsewhere"
            assert db.query(IsmsPerson).filter_by(user_id=account_id).count() == 1
            db.rollback()
    finally:
        with Session(engine) as cleanup:
            cleanup.query(IsmsPerson).filter_by(user_id=account_id).delete()
            cleanup.query(User).filter_by(id=account_id).delete()
            cleanup.commit()
