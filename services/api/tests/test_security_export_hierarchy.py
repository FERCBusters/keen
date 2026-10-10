"""Hostile exports and cyclic hierarchy regression cases."""

from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

from app.api.utils import content_disposition_attachment
from app.api.routes.isms.personal import _org_path
from app.db.models import IsmsOrgNode, IsmsDocumentFolder
from app.security.csv import spreadsheet_cell
from app.services.hierarchy import validate_parent


@pytest.mark.parametrize(
    "filename",
    [
        "report\r\nX-Evil: yes.pdf",
        'report"; x="y.pdf',
        "品質-é.pdf",
        "\x00\t\x7f",
        "../report.pdf",
    ],
)
def test_download_header_is_ascii_and_cannot_inject_headers(filename):
    header = content_disposition_attachment(filename)
    header.encode("ascii")
    assert not any(ord(c) < 32 or ord(c) == 127 for c in header)
    assert header.startswith('attachment; filename="')
    assert header.count('"') == 2
    decoded = unquote(header.split("filename*=UTF-8''", 1)[1])
    assert "/" not in decoded and "\\" not in decoded
    if filename == "品質-é.pdf":
        assert decoded == filename


@pytest.mark.parametrize("value", [None, 0, -2, "plain", "a,b", "hello\nworld"])
def test_csv_preserves_nonformula_values(value):
    assert spreadsheet_cell(value) == value


@pytest.mark.parametrize("model", [IsmsOrgNode, IsmsDocumentFolder])
def test_hierarchy_rejects_self_descendant_and_existing_cycles(contract_db, model):
    db = contract_db
    a, b = model(name="a"), model(name="b")
    db.add_all([a, b])
    db.flush()
    b.parent_id = a.id
    db.flush()
    validate_parent(db, model, a.id, b.id)
    for parent in (a.id, b.id):
        with pytest.raises(HTTPException) as exc:
            validate_parent(db, model, parent, a.id)
        assert exc.value.status_code == 400
    a.parent_id = b.id  # Simulate pre-existing or externally imported bad data.
    db.flush()
    with pytest.raises(HTTPException):
        validate_parent(db, model, a.id)


def test_my_role_path_terminates_on_corrupt_cycle():
    a = SimpleNamespace(id="a", name="a", node_type="role")
    b = SimpleNamespace(id="b", name="b", node_type="role", parent=a)
    a.parent = b
    assert [r["id"] for r in _org_path(a)] == ["b", "a"]


def test_postgres_concurrent_reparenting_rechecks_after_lock(contract_postgres_engine):
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy.orm import Session
    from sqlalchemy import text

    engine = contract_postgres_engine
    if engine is None:
        pytest.skip("PostgreSQL required for transaction-lock regression")
    ids = [uuid.uuid4(), uuid.uuid4()]
    with Session(engine) as db:
        db.add_all([IsmsOrgNode(id=ids[0], name="A"), IsmsOrgNode(id=ids[1], name="B")])
        db.commit()
    started = Event()

    def competing_edit():
        with Session(engine) as db:
            db.execute(text("SET lock_timeout = '5s'"))
            started.set()
            with pytest.raises(HTTPException) as exc:
                validate_parent(db, IsmsOrgNode, ids[0], ids[1])
            assert exc.value.status_code == 400

    try:
        with ThreadPoolExecutor(max_workers=1) as pool, Session(engine) as first:
            validate_parent(first, IsmsOrgNode, ids[1], ids[0])
            future = pool.submit(competing_edit)
            assert started.wait(2)
            first.get(IsmsOrgNode, ids[0]).parent_id = ids[1]
            first.commit()
            future.result(timeout=10)
    finally:
        with Session(engine) as db:
            db.query(IsmsOrgNode).filter(IsmsOrgNode.id.in_(ids)).delete(
                synchronize_session=False
            )
            db.commit()
