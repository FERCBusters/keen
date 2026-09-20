from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone, date, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, distinct, func
from sqlalchemy.orm import Session

from app.api.payloads import (
    ControlClauseLinksPayload,
    ClauseControlLinksPayload,
    ClauseEvidenceUrlsPayload,
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
from app.api.utils import (
    validate_control_justification as _validate_control_justification,
    normalize_diary_details as _normalize_diary_details,
    normalize_diary_links as _normalize_diary_links,
    parse_iso_dt as _parse_iso_dt,
    try_uuid as _try_uuid,
)
from app.core.cache import cache_delete_prefix
from app.core.config import settings
from app.db.models import (
    Artifact,
    AuditLog,
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    Group,
    Mapping,
    IngestionCursor,
    Permission,
    User,
    group_permissions,
    user_groups,
)
from app.db.session import get_db
from app.ingest.bookstack import apply_bookstack_config_mappings, ingest_bookstack_all
from app.ingest.cloudwatch_logs import ingest_cloudwatch_logs_all
from app.ingest.common import apply_rules
from app.ingest.diary import add_diary_entry
from app.ingest.forgejo import ingest_forgejo_all
from app.ingest.github import ingest_github_all
from app.ingest.google_workspace import ingest_google_workspace_all
from app.ingest.jenkins import ingest_jenkins_all
from app.ingest.loki import ingest_loki_all, load_loki_queries
from app.ingest.rss import ingest_rss_all
from app.ingest.taiga import ingest_taiga_all
from app.services.control_evidence_stats import (
    clear_stats_caches,
    rebuild_framework_event_stats,
)
from app.security.auth import (
    ROLE_ADMIN,
    ROLE_NORMAL,
    require_admin,
    require_authenticated,
)
from app.security.permissions import (
    has_permission,
    normalize_group_description,
    normalize_group_name,
    normalize_permission_codes,
)
from app.security.roles import count_effective_admins, normalize_role
from app.storage.s3 import put_bytes
from app.services.entity_changelog import (
    affected_clause_ids_for_control,
    affected_control_ids_for_clause,
    clause_changelog_state,
    control_changelog_state,
    list_entity_changelogs,
    record_entity_changelog,
)

router = APIRouter()


def _bump_user_authz(db: Session, user_ids) -> None:
    ids = sorted({uid for uid in (user_ids or []) if uid})
    if not ids:
        return
    db.query(User).filter(User.id.in_(ids)).update(
        {User.authz_version: User.authz_version + 1},
        synchronize_session=False,
    )


def _group_member_ids(db: Session, group_id: uuid.UUID) -> list[uuid.UUID]:
    return [
        uid
        for (uid,) in db.query(user_groups.c.user_id)
        .filter(user_groups.c.group_id == group_id)
        .all()
    ]


def _ensure_dt_iso_for_admin(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@router.post("/v1/admin/controls/import")
def admin_import_controls(
    payload: ControlImportPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):

    framework_slug = (
        payload.framework or ""
    ).strip() or settings.default_framework_slug

    fw = db.query(Framework).filter(Framework.slug == framework_slug).one_or_none()
    if fw:
        if payload.framework_name and hasattr(fw, "name"):
            fw.name = payload.framework_name.strip() or getattr(
                fw, "name", framework_slug
            )
        if payload.framework_version and hasattr(fw, "version"):
            fw.version = payload.framework_version.strip() or getattr(
                fw, "version", None
            )
        if payload.framework_url:
            if hasattr(fw, "upstream_url"):
                fw.upstream_url = payload.framework_url.strip() or getattr(
                    fw, "upstream_url", None
                )
            if hasattr(fw, "url"):
                fw.url = payload.framework_url.strip() or getattr(fw, "url", None)
        if payload.framework_source and hasattr(fw, "source"):
            fw.source = payload.framework_source.strip() or getattr(fw, "source", None)
        if hasattr(fw, "imported_at"):
            fw.imported_at = datetime.utcnow()
    else:
        fw = Framework(slug=framework_slug)
        # Some deployments extend the Framework model with metadata columns.
        if hasattr(fw, "name"):
            fw.name = (payload.framework_name or framework_slug).strip()
        if hasattr(fw, "version"):
            fw.version = payload.framework_version or None
        if hasattr(fw, "source"):
            fw.source = (payload.framework_source or "manual_import").strip()
        if hasattr(fw, "upstream_url"):
            fw.upstream_url = payload.framework_url or None
        if hasattr(fw, "url"):
            fw.url = payload.framework_url or None
        if hasattr(fw, "imported_at"):
            fw.imported_at = datetime.utcnow()
        db.add(fw)

    created = 0
    updated = 0
    errors: list[dict] = []

    for item in payload.items:
        try:
            t = (item.type or "").strip()
            r = (item.ref or "").strip()
            if not t or not r:
                raise ValueError("type/ref required")

            existing = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework_slug,
                    ControlItem.type == t,
                    ControlItem.ref == r,
                )
                .one_or_none()
            )
            if existing:
                existing.title = item.title
                existing.in_scope = bool(item.in_scope)
                existing.tags = item.tags or {}
                existing.meta = item.metadata or {}
                updated += 1
            else:
                db.add(
                    ControlItem(
                        framework_slug=framework_slug,
                        type=t,
                        ref=r,
                        title=item.title,
                        in_scope=bool(item.in_scope),
                        tags=item.tags or {},
                        meta=item.metadata or {},
                    )
                )
                created += 1

            # Keep first-class clause rows in sync when importing legacy
            # framework YAML items of type='clause'. Clause metadata may include
            # upstream_url and optional parent_ref for hierarchy.
            if t == "clause":
                meta = item.metadata or {}
                parent_ref = str(
                    meta.get("parent_ref") or meta.get("parent") or ""
                ).strip()
                parent_id = None
                if parent_ref:
                    parent = (
                        db.query(FrameworkClause)
                        .filter(
                            FrameworkClause.framework_slug == framework_slug,
                            FrameworkClause.ref == parent_ref,
                        )
                        .one_or_none()
                    )
                    parent_id = parent.id if parent else None
                clause = (
                    db.query(FrameworkClause)
                    .filter(
                        FrameworkClause.framework_slug == framework_slug,
                        FrameworkClause.ref == r,
                    )
                    .one_or_none()
                )
                if clause:
                    clause.title = item.title or clause.title or r
                    clause.parent_clause_id = parent_id
                    clause.meta = meta
                else:
                    db.add(
                        FrameworkClause(
                            framework_slug=framework_slug,
                            ref=r,
                            title=item.title or r,
                            parent_clause_id=parent_id,
                            sort_order=0,
                            meta=meta,
                        )
                    )
        except Exception as e:
            errors.append({"ref": getattr(item, "ref", None), "error": str(e)})

    db.commit()
    return {"created": created, "updated": updated, "errors": errors}


def _is_intermediate_clause_parent(db: Session, clause: FrameworkClause) -> bool:
    """Return True for clauses such as 7.5 that sit between a parent and children.

    These clauses are headings for more specific subclauses, not direct control
    applicability buckets. Direct control mappings should live on the leaf
    subclauses, while the top-level clause can still be inferred for reporting.
    """
    if not clause.parent_clause_id:
        return False
    return (
        db.query(FrameworkClause.id)
        .filter(FrameworkClause.parent_clause_id == clause.id)
        .first()
        is not None
    )


def _validate_upstream_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 2048:
        raise HTTPException(status_code=400, detail="upstream_url is too long")
    if not value.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=400, detail="upstream_url must start with http:// or https://"
        )
    return value


def _validate_clause_evidence_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 2048:
        raise HTTPException(status_code=400, detail="evidence URL is too long")
    lowered = value.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:")) or value.startswith(
        "//"
    ):
        raise HTTPException(status_code=400, detail="Unsupported evidence URL scheme")
    if value.startswith("/") or lowered.startswith(("https://", "http://")):
        return value
    raise HTTPException(
        status_code=400,
        detail="Evidence URLs must start with http://, https://, or / for KEEN pages",
    )


def _validate_clause_evidence_title(raw: str | None, fallback_url: str) -> str:
    title = (raw or "").strip()
    if len(title) > 256:
        raise HTTPException(status_code=400, detail="Evidence title is too long")
    return title or fallback_url


def _validate_clause_evidence_mappings(
    payload: ClauseEvidenceUrlsPayload,
) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    if payload.evidence_mappings is not None:
        rows = [
            {"title": item.title, "url": item.url} for item in payload.evidence_mappings
        ]
    else:
        rows = [{"title": "", "url": raw} for raw in payload.evidence_urls or []]

    for raw in rows:
        url = _validate_clause_evidence_url(raw.get("url"))
        if not url or url in seen:
            continue
        title = _validate_clause_evidence_title(raw.get("title"), url)
        seen.add(url)
        out.append({"title": title, "url": url})
        if len(out) > 50:
            raise HTTPException(status_code=400, detail="Too many evidence mappings")
    return out


def _set_metadata_url(obj, upstream_url: str | None) -> None:
    meta = dict(getattr(obj, "meta", None) or {})
    if upstream_url:
        meta["upstream_url"] = upstream_url
    else:
        meta.pop("upstream_url", None)
        meta.pop("upstreamUrl", None)
    obj.meta = meta


@router.patch("/v1/admin/controls/{control_id}/clauses")
def admin_update_control_clauses(
    control_id: str,
    payload: ControlClauseLinksPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")

    control = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")

    seen: set[uuid.UUID] = set()
    selected: list[tuple[uuid.UUID, str]] = []
    for item in payload.clauses or []:
        applicability = (item.applicability or "").strip()
        if applicability not in {"applicable", "partially_applicable"}:
            raise HTTPException(
                status_code=400,
                detail="applicability must be 'applicable' or 'partially_applicable'",
            )

        clause = None
        if item.clause_id:
            clause = (
                db.query(FrameworkClause)
                .filter(
                    FrameworkClause.id == item.clause_id,
                    FrameworkClause.framework_slug == control.framework_slug,
                )
                .one_or_none()
            )
        elif item.ref:
            clause = (
                db.query(FrameworkClause)
                .filter(
                    FrameworkClause.framework_slug == control.framework_slug,
                    FrameworkClause.ref == item.ref.strip(),
                )
                .one_or_none()
            )
        if not clause:
            raise HTTPException(status_code=400, detail="Unknown clause for framework")
        if _is_intermediate_clause_parent(db, clause):
            # Do not persist direct mappings to intermediary clauses such as 7.5
            # when more specific child clauses such as 7.5.1 exist.
            continue
        if clause.id in seen:
            continue
        seen.add(clause.id)
        selected.append((clause.id, applicability))

    before_control = control_changelog_state(db, control)
    affected_clause_ids = affected_clause_ids_for_control(
        db, control.id, [clause_id for clause_id, _ in selected]
    )
    before_clause_states = {
        clause_id: clause_changelog_state(db, clause_id)
        for clause_id in affected_clause_ids
    }

    db.query(ControlClauseLink).filter(
        ControlClauseLink.control_item_id == control.id
    ).delete()
    now = datetime.utcnow()
    for clause_id, applicability in selected:
        db.add(
            ControlClauseLink(
                control_item_id=control.id,
                clause_id=clause_id,
                applicability=applicability,
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    record_entity_changelog(
        db,
        entity_type="control",
        entity_id=control.id,
        action="updated",
        before=before_control,
        after=control_changelog_state(db, control.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/controls/{control_id}/clauses",
    )
    for affected_clause_id, before_clause in before_clause_states.items():
        record_entity_changelog(
            db,
            entity_type="clause",
            entity_id=affected_clause_id,
            action="updated",
            before=before_clause,
            after=clause_changelog_state(db, affected_clause_id),
            user=user,
            request_method="PATCH",
            request_path=f"/v1/admin/controls/{control_id}/clauses",
        )
    db.commit()
    cache_delete_prefix()
    return {"ok": True, "count": len(selected)}


@router.patch("/v1/admin/controls/{control_id}/justification")
def admin_update_control_justification(
    control_id: str,
    payload: ControlJustificationPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    control = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")

    before_control = control_changelog_state(db, control)
    justification = _validate_control_justification(payload.justification)
    meta = dict(control.meta or {})
    if justification:
        meta["justification"] = justification
    else:
        meta.pop("justification", None)
        meta.pop("soa_justification", None)
        meta.pop("control_justification", None)
        meta.pop("applicability_justification", None)
    control.meta = meta
    db.add(control)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="control",
        entity_id=control.id,
        action="updated",
        before=before_control,
        after=control_changelog_state(db, control.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/controls/{control_id}/justification",
    )
    db.commit()
    cache_delete_prefix()
    return {"ok": True, "justification": justification}


@router.patch("/v1/admin/controls/{control_id}/upstream-url")
def admin_update_control_upstream_url(
    control_id: str,
    payload: UpstreamUrlPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    control = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    before_control = control_changelog_state(db, control)
    url = _validate_upstream_url(payload.upstream_url)
    _set_metadata_url(control, url)
    db.add(control)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="control",
        entity_id=control.id,
        action="updated",
        before=before_control,
        after=control_changelog_state(db, control.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/controls/{control_id}/upstream-url",
    )
    db.commit()
    return {"ok": True, "upstream_url": url}


@router.patch("/v1/admin/clauses/{clause_id}/upstream-url")
def admin_update_clause_upstream_url(
    clause_id: str,
    payload: UpstreamUrlPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    clause = db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")
    before_clause = clause_changelog_state(db, clause)
    url = _validate_upstream_url(payload.upstream_url)
    _set_metadata_url(clause, url)
    db.add(clause)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="clause",
        entity_id=clause.id,
        action="updated",
        before=before_clause,
        after=clause_changelog_state(db, clause.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/clauses/{clause_id}/upstream-url",
    )
    db.commit()
    return {"ok": True, "upstream_url": url}


@router.patch("/v1/admin/clauses/{clause_id}/evidence-urls")
def admin_update_clause_evidence_urls(
    clause_id: str,
    payload: ClauseEvidenceUrlsPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    clause = db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")

    before_clause = clause_changelog_state(db, clause)
    mappings = _validate_clause_evidence_mappings(payload)
    urls = [item["url"] for item in mappings]
    meta = dict(clause.meta or {})
    if mappings:
        meta["evidence_mappings"] = mappings
    else:
        meta.pop("evidence_mappings", None)
    # Drop historical/alias keys so there is one canonical storage shape.
    meta.pop("evidence_urls", None)
    meta.pop("evidenceUrls", None)
    meta.pop("evidence_links", None)
    meta.pop("evidence", None)
    clause.meta = meta
    db.add(clause)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="clause",
        entity_id=clause.id,
        action="updated",
        before=before_clause,
        after=clause_changelog_state(db, clause.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/clauses/{clause_id}/evidence-urls",
    )
    db.commit()
    return {
        "ok": True,
        "evidence_mappings": mappings,
        # Backward-compatible URL-only projection for older UI/API callers.
        "evidence_urls": urls,
        "evidence_count": len(mappings),
    }


@router.patch("/v1/admin/clauses/{clause_id}/controls")
def admin_update_clause_controls(
    clause_id: str,
    payload: ClauseControlLinksPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")

    clause = db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")

    if _is_intermediate_clause_parent(db, clause):
        before_clause = clause_changelog_state(db, clause)
        affected_control_ids = affected_control_ids_for_clause(db, clause.id, [])
        before_control_states = {
            control_id: control_changelog_state(db, control_id)
            for control_id in affected_control_ids
        }
        deleted = (
            db.query(ControlClauseLink)
            .filter(ControlClauseLink.clause_id == clause.id)
            .delete(synchronize_session=False)
        )
        db.flush()
        if deleted:
            record_entity_changelog(
                db,
                entity_type="clause",
                entity_id=clause.id,
                action="updated",
                before=before_clause,
                after=clause_changelog_state(db, clause.id),
                user=user,
                request_method="PATCH",
                request_path=f"/v1/admin/clauses/{clause_id}/controls",
            )
            for affected_control_id, before_control in before_control_states.items():
                record_entity_changelog(
                    db,
                    entity_type="control",
                    entity_id=affected_control_id,
                    action="updated",
                    before=before_control,
                    after=control_changelog_state(db, affected_control_id),
                    user=user,
                    request_method="PATCH",
                    request_path=f"/v1/admin/clauses/{clause_id}/controls",
                )
        db.commit()
        if deleted:
            cache_delete_prefix()
        return {
            "ok": True,
            "count": 0,
            "skipped_intermediate_parent": True,
            "detail": "Intermediate parent clauses are inferred from their child clauses.",
        }

    seen: set[uuid.UUID] = set()
    selected: list[tuple[uuid.UUID, str]] = []
    for item in payload.controls or []:
        applicability = (item.applicability or "").strip()
        if applicability not in {"applicable", "partially_applicable"}:
            raise HTTPException(
                status_code=400,
                detail="applicability must be 'applicable' or 'partially_applicable'",
            )

        control = None
        if item.control_id:
            control = (
                db.query(ControlItem)
                .filter(
                    ControlItem.id == item.control_id,
                    ControlItem.framework_slug == clause.framework_slug,
                )
                .one_or_none()
            )
        elif item.ref:
            control = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == clause.framework_slug,
                    ControlItem.ref == item.ref.strip(),
                )
                .one_or_none()
            )
        if not control:
            raise HTTPException(status_code=400, detail="Unknown control for framework")
        if control.id in seen:
            continue
        seen.add(control.id)
        selected.append((control.id, applicability))

    before_clause = clause_changelog_state(db, clause)
    affected_control_ids = affected_control_ids_for_clause(
        db, clause.id, [control_id for control_id, _ in selected]
    )
    before_control_states = {
        control_id: control_changelog_state(db, control_id)
        for control_id in affected_control_ids
    }

    db.query(ControlClauseLink).filter(
        ControlClauseLink.clause_id == clause.id
    ).delete()
    now = datetime.utcnow()
    for control_id, applicability in selected:
        db.add(
            ControlClauseLink(
                control_item_id=control_id,
                clause_id=clause.id,
                applicability=applicability,
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    record_entity_changelog(
        db,
        entity_type="clause",
        entity_id=clause.id,
        action="updated",
        before=before_clause,
        after=clause_changelog_state(db, clause.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/admin/clauses/{clause_id}/controls",
    )
    for affected_control_id, before_control in before_control_states.items():
        record_entity_changelog(
            db,
            entity_type="control",
            entity_id=affected_control_id,
            action="updated",
            before=before_control,
            after=control_changelog_state(db, affected_control_id),
            user=user,
            request_method="PATCH",
            request_path=f"/v1/admin/clauses/{clause_id}/controls",
        )
    db.commit()
    cache_delete_prefix()
    return {"ok": True, "count": len(selected)}


@router.post("/v1/admin/ingest/loki/run")
def admin_run_loki_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_loki_all(db)}


@router.post("/v1/admin/ingest/loki/reset")
def admin_reset_loki_cursors(
    query_name: str = "",
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reset Loki ingestion cursors to now.

    If query_name is omitted, reset all configured Loki query cursors plus any
    existing loki:* cursors. This intentionally does not delete events; it only
    moves the checkpoint so future runs start from the reset time.
    """
    now = datetime.now(timezone.utc)
    raw_query_name = (query_name or "").strip()

    if raw_query_name:
        cursor_names = [
            (
                raw_query_name
                if raw_query_name.startswith("loki:")
                else f"loki:{raw_query_name}"
            )
        ]
    else:
        cursor_names = []
        try:
            cfg = load_loki_queries(settings.loki_queries_path)
            cursor_names.extend(
                f"loki:{q.get('name')}"
                for q in (cfg.get("queries", []) or [])
                if q.get("name")
            )
        except Exception:
            # Fall back to known cursors if the config file is unavailable.
            cursor_names = []

        existing = (
            db.query(IngestionCursor).filter(IngestionCursor.name.like("loki:%")).all()
        )
        cursor_names.extend(c.name for c in existing)

    cursor_names = sorted(set(cursor_names))
    reset_cursors: list[str] = []
    for cursor_name in cursor_names:
        cur = (
            db.query(IngestionCursor)
            .filter(IngestionCursor.name == cursor_name)
            .one_or_none()
        )
        if cur is None:
            cur = IngestionCursor(name=cursor_name, last_ts=None, meta={})
            db.add(cur)
            db.flush()

        previous = _ensure_dt_iso_for_admin(cur.last_ts)
        meta = dict(cur.meta or {})
        meta.update(
            {
                "manual_reset_at": now.isoformat(),
                "manual_reset_by": getattr(user, "username", None),
                "manual_reset_previous_cursor": previous,
            }
        )
        cur.meta = meta
        cur.last_ts = now
        cur.updated_at = datetime.utcnow()
        db.add(cur)
        reset_cursors.append(cursor_name)

    db.commit()
    return {
        "ok": True,
        "reset_to": now.isoformat(),
        "count": len(reset_cursors),
        "cursors": reset_cursors,
    }


@router.post("/v1/admin/ingest/cloudwatch_logs/run")
def admin_run_cloudwatch_logs_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_cloudwatch_logs_all(db)}


@router.post("/v1/admin/ingest/github/run")
def admin_run_github_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_github_all(db)}


@router.post("/v1/admin/ingest/forgejo/run")
def admin_run_forgejo_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_forgejo_all(db)}


@router.post("/v1/admin/ingest/jenkins/run")
def admin_run_jenkins_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_jenkins_all(db)}


@router.post("/v1/admin/ingest/taiga/run")
def admin_run_taiga_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_taiga_all(db)}


@router.post("/v1/admin/ingest/bookstack/run")
def admin_run_bookstack_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_bookstack_all(db)}


@router.post("/v1/admin/ingest/rss/run")
def admin_run_rss_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_rss_all(db)}


@router.post("/v1/admin/ingest/google_workspace/run")
def admin_run_google_workspace_ingest(
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"runs": ingest_google_workspace_all(db)}


@router.get("/v1/admin/ingest/status")
def admin_ingest_status(user=Depends(require_admin)):
    """Return runtime enablement for ingestion plugins.

    Used by the Admin UI to hide "Run" buttons for disabled providers.
    """

    return {
        "enabled": {
            "loki": bool(settings.loki_enabled),
            "cloudwatch_logs": bool(settings.cloudwatch_logs_enabled),
            "github": bool(settings.github_enabled),
            "google_workspace": bool(settings.google_workspace_enabled),
            "forgejo": bool(settings.forgejo_enabled),
            "jenkins": bool(settings.jenkins_enabled),
            "taiga": bool(settings.taiga_enabled),
            "bookstack": bool(settings.bookstack_enabled),
            "rss": bool(settings.rss_enabled),
        }
    }


# -----------------------------------------------------------------------------
# RBAC Admin: Groups + Permissions
# -----------------------------------------------------------------------------


@router.get("/v1/admin/permissions", response_model=list[PermissionOut])
def admin_list_permissions(
    user=Depends(require_admin), db: Session = Depends(get_db)
) -> list[PermissionOut]:
    perms = db.query(Permission).order_by(Permission.code.asc()).all()
    return [
        PermissionOut(
            id=p.id,
            code=p.code,
            description=p.description,
            created_at=p.created_at,
        )
        for p in perms
    ]


@router.post("/v1/admin/permissions", response_model=PermissionOut)
def admin_create_permission(
    payload: PermissionCreatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> PermissionOut:
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Permission code is required")
    if any(ch.isspace() for ch in code):
        raise HTTPException(
            status_code=400, detail="Permission code cannot contain whitespace"
        )
    if len(code) > 128:
        raise HTTPException(status_code=400, detail="Permission code too long")

    existing = db.query(Permission).filter(Permission.code == code).one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Permission code already exists")

    p = Permission(code=code, description=(payload.description or None))
    db.add(p)
    db.commit()
    db.refresh(p)
    return PermissionOut(
        id=p.id, code=p.code, description=p.description, created_at=p.created_at
    )


@router.get("/v1/admin/groups", response_model=list[GroupOut])
def admin_list_groups(
    user=Depends(require_admin), db: Session = Depends(get_db)
) -> list[GroupOut]:
    rows = (
        db.query(
            Group,
            func.count(distinct(user_groups.c.user_id)).label("member_count"),
            func.count(distinct(group_permissions.c.permission_id)).label(
                "permission_count"
            ),
        )
        .outerjoin(user_groups, user_groups.c.group_id == Group.id)
        .outerjoin(group_permissions, group_permissions.c.group_id == Group.id)
        .group_by(Group.id)
        .order_by(Group.name.asc())
        .all()
    )

    out: list[GroupOut] = []
    for g, member_count, perm_count in rows:
        out.append(
            GroupOut(
                id=g.id,
                name=g.name,
                description=g.description,
                role=g.role,
                created_at=g.created_at,
                member_count=int(member_count or 0),
                permission_count=int(perm_count or 0),
            )
        )
    return out


@router.post("/v1/admin/groups", response_model=GroupOut)
def admin_create_group(
    payload: GroupCreatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupOut:
    name = normalize_group_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Group name too long")

    if db.query(Group.id).filter(Group.name == name).first():
        raise HTTPException(status_code=400, detail="Group name already exists")

    role = normalize_role(payload.role)
    if role == "inherit":
        # Only users can inherit.
        role = None
    if payload.role is not None and (payload.role or "").strip() and role is None:
        raise HTTPException(
            status_code=400, detail="group role must be 'admin', 'normal', or blank"
        )

    g = Group(
        name=name,
        description=normalize_group_description(payload.description),
        role=(
            ROLE_ADMIN
            if role == ROLE_ADMIN
            else (ROLE_NORMAL if role == ROLE_NORMAL else None)
        ),
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return GroupOut(
        id=g.id,
        name=g.name,
        description=g.description,
        role=g.role,
        created_at=g.created_at,
        member_count=0,
        permission_count=0,
    )


@router.get("/v1/admin/groups/{group_id}", response_model=GroupDetailOut)
def admin_get_group(
    group_id: str,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    gid = _try_uuid(group_id)
    if not gid:
        raise HTTPException(status_code=400, detail="group_id must be a UUID")

    g = db.query(Group).filter(Group.id == gid).one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")

    user_ids = [
        uid
        for (uid,) in db.query(user_groups.c.user_id)
        .filter(user_groups.c.group_id == gid)
        .all()
    ]
    perm_codes = [
        code
        for (code,) in (
            db.query(Permission.code)
            .join(group_permissions, Permission.id == group_permissions.c.permission_id)
            .filter(group_permissions.c.group_id == gid)
            .order_by(Permission.code.asc())
            .all()
        )
    ]

    return GroupDetailOut(
        id=g.id,
        name=g.name,
        description=g.description,
        role=g.role,
        created_at=g.created_at,
        user_ids=user_ids,
        permission_codes=perm_codes,
    )


@router.patch("/v1/admin/groups/{group_id}", response_model=GroupDetailOut)
def admin_update_group(
    group_id: str,
    payload: GroupUpdatePayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    gid = _try_uuid(group_id)
    if not gid:
        raise HTTPException(status_code=400, detail="group_id must be a UUID")

    g = db.query(Group).filter(Group.id == gid).one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    affected_user_ids = _group_member_ids(db, gid)

    if payload.name is not None:
        name = normalize_group_name(payload.name)
        if not name:
            raise HTTPException(status_code=400, detail="Group name is required")
        if len(name) > 64:
            raise HTTPException(status_code=400, detail="Group name too long")
        existing = (
            db.query(Group.id).filter(Group.name == name, Group.id != gid).first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Group name already exists")
        g.name = name

    if payload.description is not None:
        g.description = normalize_group_description(payload.description)

    if payload.role is not None:
        raw = (payload.role or "").strip()
        if not raw:
            # Allow clearing the group role.
            new_role = None
        else:
            nr = normalize_role(raw)
            if nr not in (ROLE_ADMIN, ROLE_NORMAL):
                raise HTTPException(
                    status_code=400,
                    detail="group role must be 'admin', 'normal', or blank",
                )
            new_role = nr

        # Prevent lockout: don't remove the last effective admin.
        if (g.role or "") == ROLE_ADMIN and (new_role or "") != ROLE_ADMIN:
            if count_effective_admins(db) <= 1:
                raise HTTPException(
                    status_code=400, detail="Cannot remove the last effective admin"
                )

        g.role = new_role

    db.add(g)
    _bump_user_authz(db, affected_user_ids)
    db.commit()
    db.refresh(g)
    return admin_get_group(str(gid), user=user, db=db)


@router.delete("/v1/admin/groups/{group_id}")
def admin_delete_group(
    group_id: str,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    gid = _try_uuid(group_id)
    if not gid:
        raise HTTPException(status_code=400, detail="group_id must be a UUID")

    g = db.query(Group).filter(Group.id == gid).one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")

    affected_user_ids = _group_member_ids(db, gid)

    # Prevent lockout: deleting an admin-role group might remove the last effective admin.
    if (g.role or "") == ROLE_ADMIN:
        # If there are no explicit admins, this group could be the only source of admin.
        # We do a conservative check: if only one effective admin exists, block deletion.
        if count_effective_admins(db) <= 1:
            raise HTTPException(
                status_code=400, detail="Cannot remove the last effective admin"
            )

    db.delete(g)
    _bump_user_authz(db, affected_user_ids)
    db.commit()
    return {"ok": True}


@router.put("/v1/admin/groups/{group_id}/members", response_model=GroupDetailOut)
def admin_set_group_members(
    group_id: str,
    payload: GroupMembersPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    gid = _try_uuid(group_id)
    if not gid:
        raise HTTPException(status_code=400, detail="group_id must be a UUID")

    g = db.query(Group).filter(Group.id == gid).one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")

    ids = sorted(set(payload.user_ids or []))
    if ids:
        found = {uid for (uid,) in db.query(User.id).filter(User.id.in_(ids)).all()}
        missing = [str(uid) for uid in ids if uid not in found]
        if missing:
            raise HTTPException(status_code=400, detail={"missing_user_ids": missing})

    old_user_ids = _group_member_ids(db, gid)

    # Prevent lockout: if this is an admin-role group, ensure at least one admin remains after member changes.
    if (g.role or "") == ROLE_ADMIN:
        # If there are no explicit admins and we're about to clear members, block.
        # We apply the change in a transaction-like manner: compute result first.
        if (not ids) and count_effective_admins(db) <= 1:
            raise HTTPException(
                status_code=400, detail="Cannot remove the last effective admin"
            )

    db.execute(user_groups.delete().where(user_groups.c.group_id == gid))
    if ids:
        rows = [{"user_id": uid, "group_id": gid} for uid in ids]
        db.execute(user_groups.insert(), rows)

    _bump_user_authz(db, set(old_user_ids) | set(ids))
    db.commit()
    return admin_get_group(str(gid), user=user, db=db)


@router.put("/v1/admin/groups/{group_id}/permissions", response_model=GroupDetailOut)
def admin_set_group_permissions(
    group_id: str,
    payload: GroupPermissionsPayload,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupDetailOut:
    gid = _try_uuid(group_id)
    if not gid:
        raise HTTPException(status_code=400, detail="group_id must be a UUID")

    g = db.query(Group).filter(Group.id == gid).one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    affected_user_ids = _group_member_ids(db, gid)

    codes = normalize_permission_codes(payload.permission_codes or [])
    perm_ids: list[uuid.UUID] = []

    if codes:
        perms = db.query(Permission).filter(Permission.code.in_(codes)).all()
        by_code = {p.code: p for p in perms}
        missing = [c for c in codes if c not in by_code]
        if missing:
            raise HTTPException(
                status_code=400, detail={"missing_permission_codes": missing}
            )
        perm_ids = [by_code[c].id for c in codes]

    db.execute(group_permissions.delete().where(group_permissions.c.group_id == gid))
    if perm_ids:
        rows = [{"group_id": gid, "permission_id": pid} for pid in perm_ids]
        db.execute(group_permissions.insert(), rows)

    _bump_user_authz(db, affected_user_ids)
    db.commit()
    return admin_get_group(str(gid), user=user, db=db)


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
        body = await request.json()
        summary = (body.get("summary") or "").strip()
        details_obj = _normalize_diary_details(body.get("details"))

        links = _normalize_diary_links(body.get("links") or body.get("link"))
        # Optional: allow passing control refs
        raw_controls = body.get("controls") or body.get("control_refs") or []
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

        # controls[]
        try:
            controls = [
                str(c).strip() for c in form.getlist("controls") if str(c).strip()
            ]
        except Exception:
            # Some clients may send a single field
            raw = form.get("controls")
            if raw:
                controls = [c.strip() for c in str(raw).split(",") if c.strip()]

        raw_framework = form.get("framework")
        if isinstance(raw_framework, str) and raw_framework.strip():
            framework = raw_framework.strip()

        # links (optional)
        links_raw = form.get("links")
        if links_raw:
            links = _normalize_diary_links(str(links_raw))
        else:
            try:
                urls = [
                    str(x).strip() for x in form.getlist("links_url") if str(x).strip()
                ]
            except Exception:
                urls = []
            try:
                texts = [str(x).strip() for x in form.getlist("links_text")]
            except Exception:
                texts = []
            if urls:
                pairs = []
                for i, u in enumerate(urls):
                    t = texts[i] if i < len(texts) else ""
                    pairs.append({"url": u, "text": t})
                links = _normalize_diary_links(pairs)

        # attachments[] (UploadFile)
        uploads = []
        try:
            uploads.extend(list(form.getlist("attachments")))
        except Exception:
            pass
        try:
            uploads.extend(list(form.getlist("images")))  # backwards compat
        except Exception:
            pass

    if not summary:
        raise HTTPException(status_code=400, detail="Missing summary")

    # Insert the diary entry event (also stores the JSON artifact)
    res = add_diary_entry(
        db,
        author=author,
        summary=summary,
        details=details_obj,
        links=links,
        timestamp=ts,
    )
    eid = _try_uuid(res.get("event_id") or "")
    if not eid:
        return res

    # Manual control mappings (chosen explicitly in the UI)
    created_mappings = 0
    if controls:
        # Normalize refs and de-dupe
        refs = sorted({c for c in controls if c and len(c) < 128})
        controls_by_ref: dict[str, ControlItem] = {}
        if refs:
            found = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework,
                    ControlItem.ref.in_(refs),
                )
                .all()
            )
            controls_by_ref = {ci.ref: ci for ci in found}

        for ref in refs:
            ci = controls_by_ref.get(ref)
            if not ci:
                # If the control isn't present yet, create a stub so we can map to it.
                ci_type = "annex_control" if ref.upper().startswith("A.") else "clause"
                ci = ControlItem(
                    framework_slug=framework, type=ci_type, ref=ref, title=None
                )
                db.add(ci)
                db.flush()
                controls_by_ref[ref] = ci

            # Avoid duplicates (unique constraint on event_id + control_item_id)
            exists = (
                db.query(Mapping.id)
                .filter(Mapping.event_id == eid, Mapping.control_item_id == ci.id)
                .scalar()
            )
            if exists:
                continue

            mp = Mapping(
                event_id=eid,
                control_item_id=ci.id,
                confidence=1.0,
                method="manual",
                rationale="manual diary selection",
                mapped_by=author or "user",
            )
            db.add(mp)
            created_mappings += 1

    # Attach uploaded images as additional artifacts
    created_artifacts = 0
    if uploads:
        base_dt = ts or datetime.now(timezone.utc)
        date_part = base_dt.date().isoformat()
        for i, up in enumerate(uploads[:10]):
            # FormData may contain plain strings as well; ignore those.
            if not hasattr(up, "read"):
                continue
            try:
                data = await up.read()
            except Exception:
                continue
            if not data:
                continue
            ctype_up = getattr(up, "content_type", None) or "application/octet-stream"
            kind = (
                "diary_image" if ctype_up.startswith("image/") else "diary_attachment"
            )
            filename = getattr(up, "filename", None) or "upload"
            ext = os.path.splitext(filename)[1]
            if ext and len(ext) > 12:
                ext = ext[:12]
            if not ext:
                ext = ".bin"
            key = f"diary/{date_part}/{eid}/attachment-{i+1}{ext}"
            stored = put_bytes(key=key, data=data, content_type=ctype_up)
            art = Artifact(
                event_id=eid,
                kind=kind,
                storage_uri=stored.uri,
                sha256=stored.sha256,
                content_type=ctype_up,
                size_bytes=stored.size_bytes,
                captured_by="keen:diary",
            )
            db.add(art)
            created_artifacts += 1

    db.commit()
    res["manual_mappings_created"] = created_mappings
    res["attachments_created"] = created_artifacts
    return res


@router.patch("/v1/admin/diary/{event_id}")
async def admin_update_diary(
    event_id: str,
    request: Request,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit an existing diary entry (admin-only).

    Supports JSON PATCH-like semantics (only fields present are updated).

    Payload supports:
      - summary: str
      - details: str | dict
      - links: list[{url,text}] | JSON string
      - timestamp: ISO8601 string
      - controls: list[str] | comma-separated string

    Note: This endpoint is intentionally restricted to events where source=="diary".
    """

    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="event_id must be a UUID")

    e = db.query(Event).filter(Event.id == eid).one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")
    if (e.source or "") != "diary":
        raise HTTPException(status_code=400, detail="Only diary events can be edited")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    editor = getattr(getattr(request.state, "user", None), "username", None)

    # Start from current normalized payload
    np = dict(e.normalized_payload or {})

    # Summary
    if "summary" in body:
        summary = str(body.get("summary") or "").strip()
        if not summary:
            raise HTTPException(status_code=400, detail="Missing summary")
        np["summary"] = summary
        e.summary = f"diary: {summary}"

    # Details
    if "details" in body:
        details_obj = _normalize_diary_details(body.get("details"))
        np["details"] = details_obj or {}

    # Links
    if "links" in body or "link" in body:
        raw_links = body.get("links") if "links" in body else body.get("link")
        links = _normalize_diary_links(raw_links)
        if links:
            np["links"] = links
        else:
            np.pop("links", None)

    # Timestamp
    if "timestamp" in body:
        ts = _parse_iso_dt(body.get("timestamp"))
        if not ts:
            raise HTTPException(status_code=400, detail="Invalid timestamp")
        e.timestamp = ts

    # Persist normalized payload
    e.normalized_payload = np

    # Record an updated diary payload as an additional artifact (keeps history)
    try:
        payload = {
            "summary": np.get("summary"),
            "details": np.get("details") or {},
            "author": np.get("author") or e.actor,
        }
        if np.get("links"):
            payload["links"] = np.get("links")
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        base_dt = e.timestamp
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        date_part = base_dt.date().isoformat()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        key = f"diary/{date_part}/{str(e.id)}/edit-{stamp}.json"
        stored = put_bytes(key=key, data=payload_bytes, content_type="application/json")
        art = Artifact(
            event_id=eid,
            kind="diary_entry_edit",
            storage_uri=stored.uri,
            sha256=stored.sha256,
            content_type="application/json",
            size_bytes=stored.size_bytes,
            captured_by="keen:diary",
            meta={"edited_by": editor or "user"},
        )
        db.add(art)
        artifact_created = True
    except Exception:
        artifact_created = False

    # Control mappings (replace semantics when provided)
    mappings_set = None
    if "controls" in body or "control_refs" in body:
        framework = (
            str(body.get("framework") or "").strip() or settings.default_framework_slug
        )

        raw_controls = body.get("controls") or body.get("control_refs") or []
        controls: list[str] = []
        if isinstance(raw_controls, str):
            controls = [c.strip() for c in raw_controls.split(",") if c.strip()]
        elif isinstance(raw_controls, list):
            controls = [str(c).strip() for c in raw_controls if str(c).strip()]

        refs = sorted({c for c in controls if c and len(c) < 128})

        # For diary entries we treat mappings as fully user-managed.
        db.query(Mapping).filter(Mapping.event_id == eid).delete(
            synchronize_session=False
        )

        created = 0
        controls_by_ref: dict[str, ControlItem] = {}
        if refs:
            found = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework,
                    ControlItem.ref.in_(refs),
                )
                .all()
            )
            controls_by_ref = {ci.ref: ci for ci in found}

        for ref in refs:
            ci = controls_by_ref.get(ref)
            if not ci:
                ci_type = "annex_control" if ref.upper().startswith("A.") else "clause"
                ci = ControlItem(
                    framework_slug=framework, type=ci_type, ref=ref, title=None
                )
                db.add(ci)
                db.flush()
                controls_by_ref[ref] = ci

            mp = Mapping(
                event_id=eid,
                control_item_id=ci.id,
                confidence=1.0,
                method="manual",
                rationale="manual diary selection (edit)",
                mapped_by=editor or "user",
            )
            db.add(mp)
            created += 1

        mappings_set = created

    db.commit()
    return {
        "event_id": str(eid),
        "updated": True,
        "artifact_created": artifact_created,
        "manual_mappings_set": mappings_set,
    }


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
    """(Re)apply rules to existing events.

    By default this targets events with no mappings. When include_mapped=true,
    it will also process already-mapped events (only adding missing mappings).

    Optional from_date/to_date (YYYY-MM-DD) filter by Event.timestamp.
    Optional source/system/action/outcome filters allow targeted remapping,
    for example source=jenkins.
    """

    def _coerce_range_dt(s: str | None, *, end: bool) -> datetime | None:
        if not s:
            return None
        txt = str(s).strip()
        if not txt:
            return None
        # Date-only (preferred)
        try:
            d = date.fromisoformat(txt)
            return datetime.combine(d, time.max if end else time.min)
        except Exception:
            pass
        # Datetime fallback (timezone-aware allowed)
        dt = _parse_iso_dt(txt)
        if not dt:
            return None
        try:
            dt = dt.astimezone(timezone.utc)
        except Exception:
            pass
        return dt.replace(tzinfo=None)

    def _clean_filter(v: str | None) -> str | None:
        txt = str(v or "").strip()
        return txt or None

    dt_from = _coerce_range_dt(from_date, end=False)
    dt_to = _coerce_range_dt(to_date, end=True)
    source_f = _clean_filter(source)
    system_f = _clean_filter(system)
    action_f = _clean_filter(action)
    outcome_f = _clean_filter(outcome)

    qry = db.query(Event)

    if not include_mapped:
        has_mapping = db.query(Mapping.id).filter(Mapping.event_id == Event.id).exists()
        qry = qry.filter(~has_mapping)

    if dt_from is not None:
        qry = qry.filter(Event.timestamp >= dt_from)
    if dt_to is not None:
        qry = qry.filter(Event.timestamp <= dt_to)
    if source_f is not None:
        qry = qry.filter(Event.source == source_f)
    if system_f is not None:
        qry = qry.filter(Event.system == system_f)
    if action_f is not None:
        qry = qry.filter(Event.action == action_f)
    if outcome_f is not None:
        qry = qry.filter(Event.outcome == outcome_f)

    qry = qry.order_by(desc(Event.timestamp))

    # Allow limit=0 to mean "no limit".
    try:
        lim = int(limit or 0)
    except Exception:
        lim = 0
    if lim > 0:
        qry = qry.limit(lim)

    events = qry.all()

    processed = 0
    created_total = 0
    rule_mappings_created = 0
    bookstack_config_mappings_created = 0
    for e in events:
        processed += 1
        try:
            rule_created = int(apply_rules(db, e) or 0)
            bookstack_created = int(apply_bookstack_config_mappings(db, e) or 0)
            rule_mappings_created += rule_created
            bookstack_config_mappings_created += bookstack_created
            created_total += rule_created + bookstack_created
            db.commit()
        except Exception:
            db.rollback()
            continue

    cache_keys_deleted: dict[str, int] | int = 0
    stats_rebuilt: dict[str, int] = {}
    if created_total:
        stats_rebuilt = rebuild_framework_event_stats(db, clear_cache=False)
        db.commit()
        cache_keys_deleted = clear_stats_caches()

    return {
        "processed_events": processed,
        "created_mappings": created_total,
        "rule_mappings_created": rule_mappings_created,
        "bookstack_config_mappings_created": bookstack_config_mappings_created,
        "stats_rebuilt": stats_rebuilt,
        "include_mapped": bool(include_mapped),
        "from_date": from_date,
        "to_date": to_date,
        "limit": lim,
        "filters": {
            "source": source_f,
            "system": system_f,
            "action": action_f,
            "outcome": outcome_f,
        },
        "cache_keys_deleted": cache_keys_deleted,
    }


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
    """List internal UI->API audit trail entries.

    Records are written by middleware in app/main.py.
    """

    # Allow admins, and also allow non-admin users that have been granted
    # explicit/group permission to view the audit trail (e.g. an "auditors" group).
    if (user.role or "") != "admin":
        if not has_permission(db, user, "audittrail.read"):
            raise HTTPException(status_code=403, detail="Audit access required")

    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    qry = db.query(AuditLog)

    if username:
        qry = qry.filter(AuditLog.username.ilike(f"%{username.strip()}%"))

    if path:
        qry = qry.filter(AuditLog.path.ilike(f"%{path.strip()}%"))

    if method:
        m = method.strip().upper()
        if m:
            qry = qry.filter(AuditLog.method == m)

    if status_min is not None:
        try:
            qry = qry.filter(AuditLog.status_code >= int(status_min))
        except Exception:
            pass

    if status_max is not None:
        try:
            qry = qry.filter(AuditLog.status_code <= int(status_max))
        except Exception:
            pass

    total = qry.order_by(None).count()

    rows = qry.order_by(desc(AuditLog.ts)).limit(limit).offset(offset).all()
    items = [
        {
            "id": str(r.id),
            "ts": r.ts.isoformat() if r.ts else None,
            "username": r.username,
            "method": r.method,
            "path": r.path,
            "query_string": r.query_string,
            "status_code": r.status_code,
            "duration_ms": r.duration_ms,
            "client_ip": r.client_ip,
            "user_agent": r.user_agent,
            "referer": r.referer,
        }
        for r in rows
    ]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


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
    """List semantic entity changelogs for Admin → Audit trail.

    This complements the lower-level request AuditLog by showing the user-facing
    before/after diff for Control, Clause and Risk changes.
    """

    if (user.role or "") != "admin" and not has_permission(db, user, "audittrail.read"):
        raise HTTPException(status_code=403, detail="Audit access required")
    if entity_type:
        entity_type = entity_type.strip().lower()
        if entity_type not in {"control", "clause", "risk"}:
            raise HTTPException(status_code=400, detail="Invalid entity_type")
    eid = None
    if entity_id:
        eid = _try_uuid(entity_id)
        if not eid:
            raise HTTPException(status_code=400, detail="entity_id must be a UUID")
    return list_entity_changelogs(
        db,
        entity_type=entity_type,
        entity_id=eid,
        username=username,
        q=q,
        limit=limit,
        offset=offset,
    )
