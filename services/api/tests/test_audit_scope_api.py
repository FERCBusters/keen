"""Audit scope selection through the assembled application.

An audit's scope names controls, clauses and ISMS documents. Parent clauses
stand for their leaf clauses, clauses bring the controls linked to them, and
documents bring the controls and clauses they are linked to. Names that match
nothing, or more than one record, are refused and leave the scope unchanged.
"""

import uuid

import pytest
from app.db.models import (
    ControlClauseLink,
    FrameworkClause,
    IsmsDocument,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
)

# Imported fixtures: the assembled application and an editable audit.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_audit_lifecycle_api import FW, aud  # noqa: F401


@pytest.fixture
def scope(aud):  # noqa: F811 - fixture imported above
    db = aud.db
    parent = FrameworkClause(
        framework_slug=FW, ref="8", title="Operation", sort_order=8
    )
    db.add(parent)
    db.flush()
    leaves = [
        FrameworkClause(
            framework_slug=FW,
            ref=ref,
            title=f"Clause {ref}",
            parent_clause_id=parent.id,
            sort_order=order,
        )
        for ref, order in (("8.1", 1), ("8.2", 2))
    ]
    standalone = FrameworkClause(
        framework_slug=FW, ref="9", title="Evaluation", sort_order=9
    )
    db.add_all([*leaves, standalone])
    db.flush()
    db.add(
        ControlClauseLink(
            control_item_id=aud.controls["A.2"].id,
            clause_id=leaves[1].id,
            applicability="applicable",
        )
    )
    policy = IsmsDocument(title="Operations policy")
    db.add_all(
        [policy, IsmsDocument(title="Shared title"), IsmsDocument(title="Shared title")]
    )
    db.flush()
    db.add_all(
        [
            IsmsEntityControlLink(
                entity_type="document",
                entity_id=policy.id,
                framework_slug=FW,
                control_item_id=aud.controls["A.1"].id,
            ),
            IsmsEntityClauseLink(
                entity_type="document",
                entity_id=policy.id,
                framework_slug=FW,
                clause_id=standalone.id,
            ),
        ]
    )
    db.commit()

    def put(status=200, **body):
        response = aud.call(aud.client, "PUT", "/scope", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def refs(response):
        return (
            [c["ref"] for c in response["scoped_controls"]],
            [c["ref"] for c in response["scoped_clauses"]],
            [d["title"] for d in response["scoped_documents"]],
        )

    aud.put, aud.refs, aud.policy = put, refs, policy
    return aud


def test_parent_clauses_stand_for_their_leaves_and_bring_linked_controls(scope):
    response = scope.put(clauses=["8", "8.2", " 8.2 "], controls=["A.1", "A.1"])

    # 8.2 is linked to A.2, so A.2 joins the scope with the requested A.1.
    assert scope.refs(response) == (["A.1", "A.2"], ["8.1", "8.2"], [])
    detail = scope.detail()
    assert [c["ref"] for c in detail["scoped_clauses"]] == ["8.1", "8.2"]


def test_documents_bring_the_controls_and_clauses_they_are_linked_to(scope):
    response = scope.put(documents=[str(scope.policy.id)])

    assert scope.refs(response) == (["A.1"], ["9"], ["Operations policy"])
    by_title = scope.put(documents=["Operations policy"], controls=["A.2"])
    assert scope.refs(by_title) == (["A.1", "A.2"], ["9"], ["Operations policy"])


def test_replacing_the_controls_keeps_the_clauses_and_documents(scope):
    scope.put(clauses=["8.2"], documents=["Operations policy"])

    response = scope.call(scope.client, "PUT", "/controls", json={"controls": ["A.1"]})

    assert response.status_code == 200, response.text
    # Controls brought by the kept clauses and documents stay in scope.
    assert [c["ref"] for c in response.json()["scoped_controls"]] == ["A.1", "A.2"]
    detail = scope.detail()
    assert [c["ref"] for c in detail["scoped_clauses"]] == ["8.2", "9"]
    assert [d["title"] for d in detail["scoped_documents"]] == ["Operations policy"]


@pytest.mark.parametrize(
    "body,detail",
    [
        ({"controls": ["A.9"]}, "Control not found: A.9"),
        ({"clauses": ["7.7"]}, "Clause not found: 7.7"),
        ({"clauses": [str(uuid.uuid4())]}, "Clause not found: "),
        ({"documents": ["Missing policy"]}, "ISMS document not found: Missing policy"),
        (
            {"documents": ["Shared title"]},
            "More than one ISMS document has the title Shared title; use the document id",
        ),
    ],
    ids=["control", "clause-ref", "clause-id", "document", "ambiguous-document"],
)
def test_unknown_or_ambiguous_names_leave_the_scope_unchanged(scope, body, detail):
    scope.put(controls=["A.1"])

    refused = scope.put(status=400 if "More than one" in detail else 404, **body)

    assert refused["detail"].startswith(detail)
    assert [c["ref"] for c in scope.detail()["scoped_controls"]] == ["A.1"]


def test_scope_lists_are_bounded(scope):
    refused = scope.put(status=400, controls=[f"A.{n}" for n in range(1001)])

    assert refused["detail"] == "Too many controls"
