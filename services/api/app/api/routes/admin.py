from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.errors import json_object
from app.api.routing import Router
from app.api.uploads import upload_inputs
from app.core.config import settings
from app.db.session import get_db
from app.schemas import (
    ClauseControlLinksPayload,
    ClauseEvidenceUrlsPayload,
    ControlClauseLinksPayload,
    ControlImportPayload,
    ControlJustificationPayload,
    GroupCreatePayload,
    GroupDetailOut,
    GroupMembersPayload,
    GroupOut,
    GroupPermissionsPayload,
    GroupUpdatePayload,
    PermissionCreatePayload,
    PermissionOut,
    UpstreamUrlPayload,
)
from app.security.auth import require_admin, require_authenticated
from app.services import admin as service
from app.services import cloud_posture
from app.services.presentation import (
    normalize_diary_details as _normalize_diary_details,
)
from app.services.presentation import normalize_diary_links as _normalize_diary_links
from app.services.presentation import parse_iso_dt as _parse_iso_dt

router = Router()


@router.post("/v1/admin/controls/import")
def admin_import_controls(
    payload: ControlImportPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):

    return service.admin_import_controls(payload=payload, user=user, db=db)


@router.patch("/v1/admin/controls/{control_id}/clauses")
def admin_update_control_clauses(
    control_id: str,
    payload: ControlClauseLinksPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_control_clauses(
        control_id=control_id, payload=payload, user=user, db=db
    )


@router.patch("/v1/admin/controls/{control_id}/justification")
def admin_update_control_justification(
    control_id: str,
    payload: ControlJustificationPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_control_justification(
        control_id=control_id, payload=payload, user=user, db=db
    )


@router.patch("/v1/admin/controls/{control_id}/upstream-url")
def admin_update_control_upstream_url(
    control_id: str,
    payload: UpstreamUrlPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_control_upstream_url(
        control_id=control_id, payload=payload, user=user, db=db
    )


@router.patch("/v1/admin/clauses/{clause_id}/upstream-url")
def admin_update_clause_upstream_url(
    clause_id: str,
    payload: UpstreamUrlPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_clause_upstream_url(
        clause_id=clause_id, payload=payload, user=user, db=db
    )


@router.patch("/v1/admin/clauses/{clause_id}/evidence-urls")
def admin_update_clause_evidence_urls(
    clause_id: str,
    payload: ClauseEvidenceUrlsPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_clause_evidence_urls(
        clause_id=clause_id, payload=payload, user=user, db=db
    )


@router.patch("/v1/admin/clauses/{clause_id}/controls")
def admin_update_clause_controls(
    clause_id: str,
    payload: ClauseControlLinksPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_update_clause_controls(
        clause_id=clause_id, payload=payload, user=user, db=db
    )


@router.post("/v1/admin/ingest/loki/run", status_code=202)
def admin_run_loki_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_loki_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/loki/reset")
def admin_reset_loki_cursors(
    query_name: str = "",
    connection_id: str | None = None,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    "Reset Loki ingestion cursors to now.\n\n    If query_name is omitted, reset all configured Loki query cursors plus any\n    existing cursors for the selected connections. This retains events and\n    moves the checkpoint so future runs start from the reset time.\n"
    return service.admin_reset_loki_cursors(
        query_name=query_name, connection_id=connection_id, user=user, db=db
    )


@router.post("/v1/admin/ingest/cloudwatch_logs/run", status_code=202)
def admin_run_cloudwatch_logs_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_cloudwatch_logs_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/github/run", status_code=202)
def admin_run_github_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_github_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/forgejo/run", status_code=202)
def admin_run_forgejo_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_forgejo_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/jenkins/run", status_code=202)
def admin_run_jenkins_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_jenkins_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/taiga/run", status_code=202)
def admin_run_taiga_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_taiga_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/bookstack/run", status_code=202)
def admin_run_bookstack_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_bookstack_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/imap/run", status_code=202)
def admin_run_imap_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_imap_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/rss/run", status_code=202)
def admin_run_rss_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_rss_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/google_workspace/run", status_code=202)
def admin_run_google_workspace_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.admin_run_google_workspace_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/cloud/{provider}/run", status_code=202)
def admin_run_cloud_posture(
    provider: str = Path(max_length=32),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return cloud_posture.run_provider(db=db, key=provider)


@router.get("/v1/admin/ingest/status")
def admin_ingest_status(user=Depends(require_admin)):
    'Return runtime enablement for ingestion plugins.\n\n    Used by the Admin UI to hide "Run" buttons for disabled providers.\n'
    return service.admin_ingest_status(user=user)


# -----------------------------------------------------------------------------
# RBAC Admin: Groups + Permissions
# -----------------------------------------------------------------------------


@router.get("/v1/admin/permissions", response_model=list[PermissionOut])
def admin_list_permissions(
    user=Depends(require_admin), db: Session = Depends(get_db)
) -> list[PermissionOut]:
    return service.admin_list_permissions(user=user, db=db)


@router.post("/v1/admin/permissions", response_model=PermissionOut)
def admin_create_permission(
    payload: PermissionCreatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> PermissionOut:
    return service.admin_create_permission(payload=payload, user=user, db=db)


@router.get("/v1/admin/groups", response_model=list[GroupOut])
def admin_list_groups(
    user=Depends(require_admin), db: Session = Depends(get_db)
) -> list[GroupOut]:
    return service.admin_list_groups(user=user, db=db)


@router.post("/v1/admin/groups", response_model=GroupOut)
def admin_create_group(
    payload: GroupCreatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupOut:
    return service.admin_create_group(payload=payload, user=user, db=db)


@router.get("/v1/admin/groups/{group_id}", response_model=GroupDetailOut)
def admin_get_group(
    group_id: str,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    return service.admin_get_group(group_id=group_id, user=user, db=db)


@router.patch("/v1/admin/groups/{group_id}", response_model=GroupDetailOut)
def admin_update_group(
    group_id: str,
    payload: GroupUpdatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    return service.admin_update_group(
        group_id=group_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/admin/groups/{group_id}")
def admin_delete_group(
    group_id: str,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return service.admin_delete_group(group_id=group_id, user=user, db=db)


@router.put("/v1/admin/groups/{group_id}/members", response_model=GroupDetailOut)
def admin_set_group_members(
    group_id: str,
    payload: GroupMembersPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    return service.admin_set_group_members(
        group_id=group_id, payload=payload, user=user, db=db
    )


@router.put("/v1/admin/groups/{group_id}/permissions", response_model=GroupDetailOut)
def admin_set_group_permissions(
    group_id: str,
    payload: GroupPermissionsPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    return service.admin_set_group_permissions(
        group_id=group_id, payload=payload, user=user, db=db
    )


@router.post("/v1/admin/diary")
async def admin_diary(
    request: Request,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Insert a manual diary entry.

    Supports both:
      - application/json {summary, details?, controls?}
      - multipart/form-data (summary, details?, timestamp?, controls[], attachments[])

    The author is taken from the upstream auth header (Vouch) and cannot be
    overridden by the browser.
    """

    author = getattr(getattr(request.state, "user", None), "username", None)
    ctype = (request.headers.get("content-type") or "").lower()

    summary: str | None = None
    details_obj: dict | None = None
    links: list[dict[str, str]] = []
    controls: list[str] = []
    framework: str = settings.default_framework_slug
    ts: datetime | None = None
    uploads: list[Any] = []

    if "application/json" in ctype:
        body = await json_object(request)
        summary = (body.get("summary") or "").strip()
        details_obj = _normalize_diary_details(body.get("details"))

        links = _normalize_diary_links(body.get("links"))
        # Controls by id or reference, as a list or a comma-separated string.
        raw_controls = body.get("controls") or []
        if isinstance(raw_controls, str):
            controls = [c.strip() for c in raw_controls.split(",") if c.strip()]
        elif isinstance(raw_controls, list):
            controls = [str(c).strip() for c in raw_controls if str(c).strip()]

        raw_framework = body.get("framework")
        if isinstance(raw_framework, str) and raw_framework.strip():
            framework = raw_framework.strip()
    else:
        form = await request.form()
        summary = (form.get("summary") or "").strip()
        # details can be JSON or plain text
        details_obj = _normalize_diary_details(form.get("details"))

        ts = _parse_iso_dt(form.get("timestamp"))

        controls = [str(c).strip() for c in form.getlist("controls") if str(c).strip()]

        raw_framework = form.get("framework")
        if isinstance(raw_framework, str) and raw_framework.strip():
            framework = raw_framework.strip()

        # links (optional): a JSON list of {url, text}
        links_raw = form.get("links")
        if links_raw:
            links = _normalize_diary_links(str(links_raw))

        uploads = list(form.getlist("attachments"))

    return await service.admin_diary(
        db=db,
        author=author,
        summary=summary,
        details_obj=details_obj,
        links=links,
        controls=controls,
        framework=framework,
        ts=ts,
        uploads=upload_inputs(uploads),
    )


@router.patch("/v1/admin/diary/{event_id}")
async def admin_update_diary(
    event_id: str,
    request: Request,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    'Edit an existing diary entry (admin-only).\n\n    Supports JSON PATCH-like semantics (only fields present are updated).\n\n    Payload supports:\n      - summary: str\n      - details: str | dict\n      - links: list[{url,text}] | JSON string\n      - timestamp: ISO8601 string\n      - controls: list[str] | comma-separated string\n\n    Note: This endpoint is intentionally restricted to events where source=="diary".\n'
    body = await json_object(request)
    return service.admin_update_diary(
        event_id=event_id, context=change_context(request), user=user, db=db, body=body
    )


@router.post("/v1/admin/remap/run")
def admin_remap_run(
    limit: int = 500,
    include_mapped: bool = False,
    from_date: str | None = None,
    to_date: str | None = None,
    source: str | None = None,
    system: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    "(Re)apply rules to existing events.\n\n    By default this targets events with no mappings. When include_mapped=true,\n    it will also process already-mapped events (only adding missing mappings).\n\n    Optional from_date/to_date (YYYY-MM-DD) filter by Event.timestamp.\n    Optional source/system/action/outcome filters allow targeted remapping,\n    for example source=jenkins.\n"
    return service.admin_remap_run(
        limit=limit,
        include_mapped=include_mapped,
        from_date=from_date,
        to_date=to_date,
        source=source,
        system=system,
        action=action,
        outcome=outcome,
        user=user,
        db=db,
    )


@router.get("/v1/admin/audit")
def admin_audit_trail(
    limit: int = 50,
    offset: int = 0,
    username: str | None = None,
    path: str | None = None,
    method: str | None = None,
    status_min: int | None = None,
    status_max: int | None = None,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    "List internal UI->API audit trail entries.\n\n    Records are written by middleware in app/main.py.\n"
    return service.admin_audit_trail(
        limit=limit,
        offset=offset,
        username=username,
        path=path,
        method=method,
        status_min=status_min,
        status_max=status_max,
        user=user,
        db=db,
    )


@router.get("/v1/admin/entity-changelog")
def admin_entity_changelog(
    limit: int = 50,
    offset: int = 0,
    entity_type: str | None = None,
    entity_id: str | None = None,
    username: str | None = None,
    q: str | None = None,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    "List semantic entity changelogs for Admin → Audit trail.\n\n    This complements the lower-level request AuditLog by showing the user-facing\n    before/after diff for Control, Clause and Risk changes.\n"
    return service.admin_entity_changelog(
        limit=limit,
        offset=offset,
        entity_type=entity_type,
        entity_id=entity_id,
        username=username,
        q=q,
        user=user,
        db=db,
    )


@router.post("/v1/admin/ingest/gitea/run", status_code=202)
def admin_run_gitea_ingest(user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.admin_run_gitea_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/gitlab/run", status_code=202)
def admin_run_gitlab_ingest(user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.admin_run_gitlab_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/redmine/run", status_code=202)
def admin_run_redmine_ingest(
    user=Depends(require_admin), db: Session = Depends(get_db)
):
    return service.admin_run_redmine_ingest(user=user, db=db)


@router.post("/v1/admin/ingest/riskledger/run", status_code=202)
def admin_run_riskledger_ingest(
    user=Depends(require_admin), db: Session = Depends(get_db)
):
    return service.admin_run_riskledger_ingest(user=user, db=db)
