from __future__ import annotations

import hashlib
import ipaddress
import socket
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import Event, Artifact, ControlItem, Mapping
from app.mapping.rules import load_rules, evaluate_by_framework
from app.core.config import settings
from app.storage.s3 import put_bytes
from app.security.redaction import (
    combine_redaction_status,
    mask_event_data_bytes,
    mask_event_data_obj,
    mask_event_data_str,
    redact_bytes,
    redact_obj,
)


def is_safe_url(url: str) -> bool:
    """Validate that a URL does not point to disallowed addresses.

    This provides SSRF protection for ingestion plugins (Jenkins, Loki, etc.).

    SECURITY REQUIREMENTS:
    - HTTPS ONLY: All external URLs must use https:// scheme
    - TLS verification is enforced in all ingestion clients (verify=True)

    Security model:
    - Config files (jenkins.yml, loki.yml, etc.) are mounted read-only into the container
    - URL values come from admin-controlled configuration, not end-user input
    - KEEN_INGESTION_ALLOWED_CIDRS can restrict which LAN ranges are allowed

    Blocking rules:
    - Always block: http:// scheme (HTTPS required)
    - Always block: localhost, loopback (127.0.0.0/8, ::1), link-local (169.254.0.0/16)
    - Always block: multicast, unspecified addresses
    - If KEEN_INGESTION_ALLOWED_CIDRS is set: only allow those CIDR blocks
    - If KEEN_INGESTION_ALLOWED_CIDRS is empty: allow all private LAN ranges

    Returns True if the URL is safe to access, False otherwise.
    """
    if not url:
        return False

    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)

        # HTTPS ONLY - block plaintext HTTP
        if parsed.scheme.lower() != "https":
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Block localhost explicitly
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False

        # Resolve hostname and check all IP addresses
        try:
            addr_info = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            # Cannot resolve - block by default
            return False

        # Parse allowed CIDR blocks from settings
        allowed_cidrs = _parse_allowed_cidrs()

        for info in addr_info:
            family, _, _, _, sockaddr = info
            ip_str = sockaddr[0]

            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                # Invalid IP - block by default
                return False

            # Always block these dangerous addresses
            if ip.is_loopback:
                return False
            if ip.is_link_local:
                return False
            if ip.is_multicast:
                return False
            if ip.is_unspecified:
                return False
            # Block IPv4-mapped IPv6 addresses that resolve to loopback/link-local
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ipv4 = ip.ipv4_mapped
                if ipv4 and (ipv4.is_loopback or ipv4.is_link_local):
                    return False

            # If allowed_cidrs is configured, private IPs must match one of them
            # Public internet IPs are always allowed (HTTPS only, with TLS verification)
            if allowed_cidrs:
                if ip.is_private:
                    if not any(ip in network for network in allowed_cidrs):
                        return False

        return True
    except Exception:
        # Any error in validation - block by default (fail closed)
        return False


def _parse_allowed_cidrs() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse KEEN_INGESTION_ALLOWED_CIDRS into a list of IPv4Network/IPv6Network objects."""
    cidr_str = getattr(settings, "ingestion_allowed_cidrs", "") or ""
    cidr_str = cidr_str.strip()
    if not cidr_str:
        return []

    networks = []
    for part in cidr_str.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            # Invalid CIDR - ignore it but continue processing others
            pass
    return networks


def fingerprint(parts: list[str], payload: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    h.update(payload)
    return h.hexdigest()[:32]


def ensure_controls(
    db: Session, framework_slug: str, refs: list[str]
) -> dict[str, ControlItem]:
    if not refs:
        return {}
    found = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == framework_slug, ControlItem.ref.in_(refs))
        .all()
    )
    by_ref = {c.ref: c for c in found}
    for ref in refs:
        if ref not in by_ref:
            ci = ControlItem(
                framework_slug=framework_slug, type="annex_control", ref=ref, title=None
            )
            db.add(ci)
            db.flush()
            by_ref[ref] = ci
    return by_ref


def apply_rules(db: Session, ev: Event) -> int:
    rules = load_rules(settings.rules_path)
    event_dict = {
        "source": ev.source,
        "system": ev.system,
        "actor": ev.actor,
        "action": ev.action,
        "outcome": ev.outcome,
        "severity": ev.severity,
        "summary": ev.summary,
        "raw_pointer": ev.raw_pointer,
        "normalized_payload": ev.normalized_payload,
    }
    matched = evaluate_by_framework(event_dict, rules)
    if not matched:
        return 0

    # If rules are re-applied to an event that already has mappings, skip any
    # mapping pairs that already exist. This makes remapping idempotent and
    # prevents unique-constraint errors on (event_id, control_item_id).
    existing_control_ids = {
        cid
        for (cid,) in (
            db.query(Mapping.control_item_id).filter(Mapping.event_id == ev.id).all()
        )
    }

    created = 0
    for framework_slug, refs in matched.items():
        controls = ensure_controls(db, framework_slug=framework_slug, refs=refs)

        for ref in refs:
            ci = controls.get(ref)
            if not ci:
                continue
            if ci.id in existing_control_ids:
                continue
            mp = Mapping(
                event_id=ev.id,
                control_item_id=ci.id,
                confidence=0.8,
                method="rule",
                rationale=f"auto by rules ({framework_slug})",
                mapped_by="system",
            )
            db.add(mp)
            created += 1

    # Flush once (faster, and any unexpected constraint errors will surface here).
    if created:
        db.flush()
    return created


def store_event_with_artifact(
    db: Session,
    *,
    timestamp: datetime,
    source: str,
    system: Optional[str],
    actor: Optional[str],
    action: Optional[str],
    outcome: Optional[str],
    severity: Optional[int],
    summary: str,
    raw_pointer: dict[str, Any],
    normalized_payload: dict[str, Any],
    external_id: str,
    artifact_kind: str,
    artifact_bytes: bytes,
    artifact_content_type: str,
    artifact_key: str,
    captured_by: str,
) -> dict[str, Any]:
    # --- hardening: redact secrets BEFORE persisting anything ---
    raw_pointer = redact_obj(raw_pointer) or {}
    normalized_payload = redact_obj(normalized_payload) or {}

    artifact_bytes, redaction_status = redact_bytes(
        artifact_bytes, artifact_content_type
    )

    # Optional privacy masking at ingestion time.  The "samples" mode is
    # intentionally not applied here; it is applied dynamically during evidence
    # PDF/ZIP export so the main Keen UI retains the original event data.
    if (settings.event_data_masking or "false") == "true":
        system = mask_event_data_str(system)
        actor = mask_event_data_str(actor)
        action = mask_event_data_str(action)
        outcome = mask_event_data_str(outcome)
        summary = mask_event_data_str(summary) or ""
        raw_pointer = mask_event_data_obj(raw_pointer) or {}
        normalized_payload = mask_event_data_obj(normalized_payload) or {}
        artifact_bytes, masking_status = mask_event_data_bytes(
            artifact_bytes, artifact_content_type
        )
        redaction_status = combine_redaction_status(redaction_status, masking_status)

    # Fast de-dupe check (avoids triggering a rollback that would also undo
    # ingestion cursor updates that may be pending in the same session).
    existing = (
        db.query(Event.id)
        .filter(Event.source == source, Event.external_id == external_id)
        .one_or_none()
    )
    if existing:
        return {"ok": True, "deduped": True, "event_id": str(existing[0])}

    ev = Event(
        timestamp=timestamp,
        source=source,
        system=system,
        actor=actor,
        action=action,
        outcome=outcome,
        severity=severity,
        summary=summary,
        raw_pointer=raw_pointer,
        normalized_payload=normalized_payload,
        external_id=external_id,
    )
    try:
        # Use a SAVEPOINT so an integrity error here doesn't force a full
        # transaction rollback (which would also undo cursor changes).
        with db.begin_nested():
            db.add(ev)
            db.flush()
    except IntegrityError:
        # Race: another worker inserted the same event.
        existing_id = (
            db.query(Event.id)
            .filter(Event.source == source, Event.external_id == external_id)
            .scalar()
        )
        return {
            "ok": True,
            "deduped": True,
            "event_id": str(existing_id) if existing_id else None,
        }

    stored = put_bytes(
        key=artifact_key, data=artifact_bytes, content_type=artifact_content_type
    )
    art = Artifact(
        event_id=ev.id,
        kind=artifact_kind,
        storage_uri=stored.uri,
        sha256=stored.sha256,
        content_type=artifact_content_type,
        size_bytes=stored.size_bytes,
        captured_by=captured_by,
        redaction_status=redaction_status,
    )
    db.add(art)
    db.flush()

    created_mappings = apply_rules(db, ev)
    db.commit()
    return {
        "ok": True,
        "event_id": str(ev.id),
        "artifact_uri": stored.uri,
        "sha256": stored.sha256,
        "mappings": created_mappings,
    }
