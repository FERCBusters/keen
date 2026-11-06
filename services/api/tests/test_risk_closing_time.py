"""A risk records when its treatment closed, through every route that sets it.

Posture results compare the closing time with when their failure began, so a
closed risk treats a failure only from the moment it closed. The time is set
as the status becomes closed, kept while it stays closed and cleared when the
risk reopens. ``services.risks.set_treatment_status`` is the only writer.
"""

import ast
import uuid
from datetime import datetime
from pathlib import Path

from app.db.models import Risk
from app.services import risks as risk_service

# Imported fixtures: the assembled application and a signed-in risk manager.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_risk_api_workflows import _create, reg  # noqa: F401
from tests.test_risk_register_api_workflows import _csv, _import, _rating

APP = Path(__file__).resolve().parents[1] / "app"


def clock(monkeypatch, start):
    """The service's clock, set by the test."""
    now = {"value": start}
    monkeypatch.setattr(risk_service, "_utcnow", lambda: now["value"])
    return now


def stored(reg, risk_id):  # noqa: F811
    reg.db.expire_all()
    return reg.db.get(Risk, uuid.UUID(risk_id))


def test_closing_a_risk_records_the_time_and_reopening_clears_it(
    reg,  # noqa: F811
    monkeypatch,
):
    first, second = datetime(2026, 11, 2, 9, 30), datetime(2026, 11, 5, 14, 0)
    risk = _create(reg, treatment_status="open")
    assert stored(reg, risk["id"]).closed_at is None

    now = clock(monkeypatch, first)
    path = f"/v1/risks/{risk['id']}"
    reg.manage("PATCH", path, {"treatment_status": "closed"})
    assert stored(reg, risk["id"]).closed_at == first
    # Saving a closed risk again later keeps the time it closed.
    now["value"] = datetime(2026, 11, 3, 12, 0)
    reg.manage("PATCH", path, {"treatment_status": "closed", "title": "Renamed"})
    row = stored(reg, risk["id"])
    assert (row.title, row.treatment_status, row.closed_at) == (
        "Renamed",
        "closed",
        first,
    )
    # Other edits leave the status, and its time, alone.
    reg.manage("PATCH", path, {"treatment_plan": "Rotate the export key"})
    assert stored(reg, risk["id"]).closed_at == first

    reg.manage("PATCH", path, {"treatment_status": "in_progress"})
    row = stored(reg, risk["id"])
    assert (row.treatment_status, row.closed_at) == ("in_progress", None)
    now["value"] = second
    reg.manage("PATCH", path, {"treatment_status": "closed"})
    assert stored(reg, risk["id"]).closed_at == second


def test_a_risk_created_closed_records_when(reg, monkeypatch):  # noqa: F811
    created = datetime(2026, 11, 3, 8, 0)
    clock(monkeypatch, created)
    risk = _create(reg, treatment_status="closed")
    assert stored(reg, risk["id"]).closed_at == created


def test_the_register_editor_records_the_closing_time(reg, monkeypatch):  # noqa: F811
    closed = datetime(2026, 11, 4, 16, 45)
    risk = _create(reg, treatment_status="open")
    now = clock(monkeypatch, closed)
    path = f"/v1/risks/{risk['id']}/register"
    reg.manage("PATCH", path, _rating(treatment_status="closed"))
    assert stored(reg, risk["id"]).closed_at == closed
    now["value"] = datetime(2026, 11, 5, 9, 0)
    reg.manage("PATCH", path, _rating(treatment_status="closed"))
    assert stored(reg, risk["id"]).closed_at == closed
    reg.manage("PATCH", path, _rating(treatment_status="awaiting_review"))
    row = stored(reg, risk["id"])
    assert (row.treatment_status, row.closed_at) == ("awaiting_review", None)


def test_imported_closed_risks_record_when_they_were_imported(
    reg,  # noqa: F811
    monkeypatch,
):
    imported = datetime(2026, 11, 5, 10, 15)
    clock(monkeypatch, imported)
    _import(
        reg,
        _csv(
            "Laptop fleet,Devices,Laptops,Stolen laptop,,,,3,4,2,2,mitigate,"
            "closed,Encrypted disks,,",
            "Payroll,Data,Databases,Salary export,,,,3,4,2,2,mitigate,"
            "open,Restrict exports,,",
        ),
    )
    rows = {r.threat_summary: r for r in reg.db.query(Risk).all()}
    assert (
        rows["Stolen laptop"].treatment_status,
        rows["Stolen laptop"].closed_at,
    ) == (
        "closed",
        imported,
    )
    assert (
        rows["Salary export"].treatment_status,
        rows["Salary export"].closed_at,
    ) == (
        "open",
        None,
    )


def status_writes(source: str) -> list[int]:
    """Lines that set a treatment status, outside the shared setter."""
    found = []

    def sets_status(target) -> bool:
        if isinstance(target, ast.Attribute):
            return target.attr == "treatment_status"
        if isinstance(target, ast.Tuple):
            return any(sets_status(t) for t in target.elts)
        return False

    def visit(node, inside_setter=False):
        if isinstance(node, ast.FunctionDef) and node.name == "set_treatment_status":
            inside_setter = True
        if not inside_setter:
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if any(sets_status(t) for t in targets):
                    found.append(node.lineno)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name == "Risk" and any(
                    k.arg == "treatment_status" for k in node.keywords
                ):
                    found.append(node.lineno)
                if (
                    name == "setattr"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "treatment_status"
                ):
                    found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, inside_setter)

    visit(ast.parse(source))
    return found


def test_treatment_status_is_set_only_through_the_shared_setter():
    found = {
        path.relative_to(APP).as_posix(): lines
        for path in sorted(APP.rglob("*.py"))
        if (lines := status_writes(path.read_text()))
    }
    assert found == {}


def test_the_check_finds_a_hand_written_status_change():
    source = (
        "risk.treatment_status = 'closed'\n"
        "row.treatment_strategy, row.treatment_status = strategy, status\n"
        "Risk(title='x', treatment_status='closed')\n"
        "setattr(row, 'treatment_status', value)\n"
        "def set_treatment_status(risk, status):\n"
        "    risk.treatment_status = status\n"
    )
    assert status_writes(source) == [1, 2, 3, 4]
