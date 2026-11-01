"""Database-managed collection settings and unified evidence definitions."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import managed_configurations as service
from app.services import rule_insights as insights
from app.services.managed_configurations import (
    AdapterSettingsInput,
    ConnectionRemovalInput,
    ControlSuggestionInput,
    DocumentInput,
    EvidenceDefinitionInput,
    PruneInput,
    RestoreInput,
    RuleInput,
    SourcePauseInput,
)

router = Router(dependencies=[Depends(require_admin)])

# Credential material stays in Docker environment variables, not editable JSON.


@router.post("/v1/admin/control-suggestions")
def suggest_controls(payload: ControlSuggestionInput, db: Session = Depends(get_db)):
    "Explainable catalogue and related-concept suggestions, for human review."
    return service.suggest_controls(payload=payload, db=db)


@router.get("/v1/admin/adapter-settings/{adapter}")
def get_adapter_settings(adapter: str, db: Session = Depends(get_db)):
    return service.get_adapter_settings(adapter=adapter, db=db)


@router.put("/v1/admin/adapter-settings/{adapter}")
def put_adapter_settings(
    adapter: str,
    payload: AdapterSettingsInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.put_adapter_settings(
        adapter=adapter, payload=payload, user=user, db=db
    )


@router.get("/v1/admin/managed-configurations")
def list_configurations(db: Session = Depends(get_db)):
    return service.list_configurations(db=db)


@router.get("/v1/admin/managed-configurations/{name}")
def get_configuration(name: str, db: Session = Depends(get_db)):
    return service.get_configuration(name=name, db=db)


@router.put("/v1/admin/managed-configurations/{name}")
def put_configuration(
    name: str,
    payload: DocumentInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.put_configuration(name=name, payload=payload, user=user, db=db)


@router.get("/v1/admin/evidence-definitions")
def get_evidence_definitions(db: Session = Depends(get_db)):
    "Show collection items alongside source-wide and item-specific definitions."
    return service.get_evidence_definitions(db=db)


@router.get("/v1/admin/source-catalogue")
def source_catalogue(db: Session = Depends(get_db)):
    "Show supported adapters, collection setup and observed evidence."
    return service.source_catalogue(db=db)


@router.get("/v1/admin/bookstack/catalog")
def bookstack_catalog(
    kind: str = "books",
    book_id: int | None = None,
    offset: int = 0,
    connection_id: str | None = None,
    db: Session = Depends(get_db),
):
    "Small, metadata-only BookStack pages; credentials remain on the server."
    return service.bookstack_catalog(
        kind=kind, book_id=book_id, offset=offset, connection_id=connection_id, db=db
    )


@router.put("/v1/admin/evidence-definitions/{rule_id}")
def save_evidence_definition(
    rule_id: str,
    payload: EvidenceDefinitionInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    "Publish a collection item and mapping rule in one database transaction."
    return service.save_evidence_definition(
        rule_id=rule_id, payload=payload, user=user, db=db
    )


@router.get("/v1/admin/mapping-rules/export.yaml")
def export_rules(source: str = "", db: Session = Depends(get_db)):
    """Export effective rules in the same format consumed by KEEN_RULES_PATH."""
    from fastapi.responses import Response

    content, filename = service.export_rules(source=source, db=db)
    return Response(
        content,
        media_type="application/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="keen-rules-{filename}.yaml"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/v1/admin/mapping-rules")
def get_rules(db: Session = Depends(get_db)):
    return service.get_rules(db=db)


@router.get("/v1/admin/mapping-rules/insights")
def rule_insights(days: int = insights.DEFAULT_DAYS, db: Session = Depends(get_db)):
    "Each rule's recent activity, and rules that could be consolidated."
    return insights.rule_insights(days=days, db=db)


@router.put("/v1/admin/mapping-rules/{rule_id}")
def put_rule(
    rule_id: str,
    payload: RuleInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.put_rule(rule_id=rule_id, payload=payload, user=user, db=db)


@router.delete("/v1/admin/mapping-rules/{rule_id}")
def delete_rule(
    rule_id: str,
    version: int,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.delete_rule(rule_id=rule_id, version=version, user=user, db=db)


@router.post("/v1/admin/mapping-rules/preview")
def preview_rule(payload: RuleInput, db: Session = Depends(get_db)):
    # Validation also prevents a typo from creating placeholder controls.
    return service.preview_rule(payload=payload, db=db)


@router.get("/v1/admin/evidence-event-samples")
def evidence_event_samples(
    source: str | None = None,
    collector: str | None = None,
    connection_id: str | None = None,
    db: Session = Depends(get_db),
):
    "Expose the event fields used by exact-match rule conditions."
    return service.evidence_event_samples(
        source=source, collector=collector, connection_id=connection_id, db=db
    )


@router.get("/v1/admin/mapping-rules/{rule_id}/revisions")
def list_rule_revisions(rule_id: str, db: Session = Depends(get_db)):
    return service.list_rule_revisions(rule_id=rule_id, db=db)


@router.post("/v1/admin/mapping-rules/{rule_id}/revisions/{revision}/restore")
def restore_rule_revision(
    rule_id: str,
    revision: int,
    payload: RestoreInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.restore_rule_revision(
        rule_id=rule_id, revision=revision, payload=payload, user=user, db=db
    )


@router.get("/v1/admin/configuration-revisions/{name}")
def list_revisions(name: str, db: Session = Depends(get_db)):
    return service.list_revisions(name=name, db=db)


@router.post("/v1/admin/configuration-revisions/{name}/{revision}/restore")
def restore_revision(
    name: str,
    revision: int,
    payload: RestoreInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.restore_revision(
        name=name, revision=revision, payload=payload, user=user, db=db
    )


@router.get("/v1/admin/stale-rule-mappings")
def preview_stale_rule_mappings(
    source: str, offset: int = 0, db: Session = Depends(get_db)
):
    "Review one bounded page of a source's rule mappings; manual mappings stay out."
    return service.preview_stale_rule_mappings(source=source, offset=offset, db=db)


@router.post("/v1/admin/stale-rule-mappings/prune")
def prune_stale_rule_mappings(payload: PruneInput, db: Session = Depends(get_db)):
    "Recheck reviewed IDs against current rules before removing rule mappings."
    return service.prune_stale_rule_mappings(payload=payload, db=db)


@router.get("/v1/admin/rule-backfills/{job_id}")
def get_rule_backfill(job_id: str, db: Session = Depends(get_db)):
    return service.get_rule_backfill(job_id=job_id, db=db)


@router.post("/v1/admin/rule-backfills/{job_id}/cancel")
def cancel_rule_backfill(job_id: str, db: Session = Depends(get_db)):
    return service.cancel_rule_backfill(job_id=job_id, db=db)


@router.post("/v1/admin/mapping-rules/{rule_id}/backfill")
def start_rule_backfill(
    rule_id: str, user=Depends(require_admin), db: Session = Depends(get_db)
):
    return service.start_rule_backfill(rule_id=rule_id, user=user, db=db)


@router.get("/v1/admin/rule-backfills")
def list_rule_backfills(rule_id: str | None = None, db: Session = Depends(get_db)):
    return service.list_rule_backfills(rule_id=rule_id, db=db)


@router.get("/v1/admin/evidence-connections/{key}/removal")
def preview_connection_removal(key: str, db: Session = Depends(get_db)):
    return service.preview_connection_removal(key=key, db=db)


@router.post("/v1/admin/evidence-connections/{key}/remove")
def remove_evidence_connection(
    key: str,
    payload: ConnectionRemovalInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.remove_evidence_connection(
        key=key, payload=payload, user=user, db=db
    )


@router.put("/v1/admin/sources/{source}/ingestion-state")
def set_source_ingestion_state(
    source: str,
    payload: SourcePauseInput,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    return service.set_source_ingestion_state(
        source=source, payload=payload, db=db, user=user
    )
