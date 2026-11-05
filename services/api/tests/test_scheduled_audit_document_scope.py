"""A scheduled audit carries its template's ISMS document scope."""

import uuid

from app.db.models import Audit, AuditScopedIsmsDocument, IsmsDocument
from app.services import audit_schedules as schedules

# Imported fixture: the mailer replaced at its owning module.
from tests.test_audit_schedule_materialisation import TODAY, _persisted_template
from tests.test_audit_schedule_materialisation import mailer as mailer


def test_each_scheduled_audit_scopes_the_documents_of_its_template(contract_db, mailer):
    db = contract_db
    template = _persisted_template(db)
    policy = IsmsDocument(title="Access control policy")
    procedure = IsmsDocument(title="Joiners and leavers procedure")
    unrelated = IsmsDocument(title="Travel policy")
    db.add_all([policy, procedure, unrelated])
    db.flush()
    db.add_all(
        [
            AuditScopedIsmsDocument(audit_id=template.id, document_id=policy.id),
            AuditScopedIsmsDocument(audit_id=template.id, document_id=procedure.id),
        ]
    )
    db.commit()

    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    (created,) = result["created"]
    audit = db.get(Audit, uuid.UUID(created["audit_id"]))

    def scoped(audit_id):
        return {
            row.document_id
            for row in db.query(AuditScopedIsmsDocument).filter_by(audit_id=audit_id)
        }

    assert scoped(audit.id) == {policy.id, procedure.id}
    # The template keeps its own scope for the next occurrence.
    assert scoped(template.id) == {policy.id, procedure.id}
