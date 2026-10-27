"""Bounded, evidence-backed workstation summaries for the People register."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentPostureObservation, Event, IsmsPerson, KeenAgent
from app.security.permissions import has_permission
from app.services.errors import DomainError
from app.services.features import state as feature_state

CHECKS = {
    "disk_encryption": "Disk encryption",
    "screen_lock": "Screen lock",
    "antivirus": "Antivirus",
    "firewall": "Firewall",
    "automatic_updates": "Automatic updates",
}
MAX_STREAMS = 128
PAGE_SIZE = 20
MAX_AGE = timedelta(hours=24)
SAFE_PROVIDER = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


def observation(event):
    payload = event.normalized_payload or {}
    f = payload.get("fields", {})
    if not isinstance(f, dict):
        return None
    check, provider, scope = (
        f.get("posture." + k, "") for k in ("check", "provider", "scope")
    )
    if (
        check not in CHECKS
        or event.action != "posture." + check
        or f.get("posture.schema_version") != "1"
        or not isinstance(provider, str)
        or not SAFE_PROVIDER.fullmatch(provider)
        or not isinstance(scope, str)
        or not scope
        or len(scope) > 512
    ):
        return None
    # Only explicitly namespaced, bounded posture metadata reaches the summary.
    return {
        k[8:]: v
        for k, v in f.items()
        if isinstance(k, str)
        and k.startswith("posture.")
        and isinstance(v, str)
        and len(v) <= 1024
    }


def record_observation(db, agent, event_id):
    """Caller holds the authenticated agent row lock and owns the transaction."""
    event = db.get(Event, event_id)
    f = observation(event)
    if agent.posture_assigned_at is None or event.timestamp < agent.posture_assigned_at:
        return
    if f is None:
        return
    key = hashlib.sha256(
        json.dumps([f["check"], f["provider"], f["scope"]]).encode()
    ).hexdigest()
    row = db.get(AgentPostureObservation, (agent.id, key))
    if row is not None:
        if event.timestamp <= row.observed_at:
            return
        row.event_id, row.observed_at = event.id, event.timestamp
    else:
        count = db.query(AgentPostureObservation).filter_by(agent_id=agent.id).count()
        if count >= MAX_STREAMS:
            # Overflow must never produce a misleading complete-device score.
            agent.health = {**agent.health, "posture_overflow": True}
            return
        db.add(
            AgentPostureObservation(
                agent_id=agent.id,
                stream_key=key,
                event_id=event.id,
                observed_at=event.timestamp,
            )
        )
    db.flush()


def _boolean(f, *keys):
    values = [f[k] for k in keys if f.get(k) in ("true", "false")]
    if "false" in values:
        return "attention"
    return "met" if values else "unknown"


def _positive(value):
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and 0 < int(value) < 2**32
    )


def evaluate(f):
    if f.get("reason") == "managed_outside_qube":
        return "external"
    if f.get("status") != "observed":
        return "unknown"
    check = f["check"]
    if check == "disk_encryption":
        if f.get("transition_in_progress") == "true":
            return "attention"
        keys = (
            ("fully_encrypted", "protection_enabled")
            if f["provider"] == "bitlocker"
            else ("encrypted", "enabled")
        )
        if f["provider"] == "bitlocker" and any(
            f.get(k) not in ("true", "false") for k in keys
        ):
            return "unknown"
        return _boolean(f, *keys)
    if check == "screen_lock":
        if "automatic_lock_configured" in f:
            return _boolean(f, "automatic_lock_configured")
        if f["provider"] == "windows_user_policy":
            # Screen saver values are one of several Windows lock paths, and
            # ScreenSaveActive defaults to 1 with no screen saver selected. Only
            # the collector's combined automatic_lock_configured fact, which also
            # covers machine inactivity policy and sign-in on wake, is a result.
            return "unknown"
        if (
            f.get("password_required") == "false"
            or f.get("saver_enabled") == "false"
            or f.get("idle_seconds") == "0"
        ):
            return "attention"
        if f.get("password_delay_seconds") == "2147483647":
            return "attention"
        if f.get("password_required") == "true" and _positive(f.get("idle_seconds")):
            return "met"
    if check == "antivirus":
        state = _boolean(
            f,
            "antivirus_enabled",
            "real_time_protection_enabled",
            "service_enabled",
            "healthy",
        )
        if state != "unknown":
            return state
        if (
            f.get("reason")
            in ("components_detected", "service_detected", "product_registered")
            or f.get("installed") == "true"
        ):
            return "met"
    if check == "firewall":
        state = _boolean(f, "enabled", "manager_active")
        if state != "unknown":
            return state
        if f.get("input_policy") in ("DROP", "REJECT") or _positive(
            f.get("input_drop_policy_chains")
        ):
            return "met"
        if f.get("input_policy") == "ACCEPT" and f.get("input_rule_count") == "0":
            return "attention"
    if check == "automatic_updates":
        if f.get("configuration_effective") == "unknown":
            return "unknown"
        if (
            f.get("scheduler_state") in ("inactive", "failed", "not_loaded")
            or f.get("policy_no_auto_update") == "1"
        ):
            return "attention"
        return _boolean(
            f,
            "automatic_install_configured",
            "automatic_os_install_configured",
            "legacy_scheduled_install_configured",
        )
    return "unknown"


def _iso(value):
    return value.isoformat() + "Z" if value is not None else None


def person_posture(*, db, user, person_id, offset=0):
    # Posture is derived evidence; People access alone never discloses it.
    from app.services.isms.access import _can_read_isms

    if not _can_read_isms(db, user) or not has_permission(db, user, "events.read"):
        raise DomainError(403, "ISMS read access and events.read are required")
    feature_state(db).require("people", "evidence")
    if offset < 0 or offset > 100000:
        raise DomainError(400, "Invalid device offset")
    if db.get(IsmsPerson, person_id) is None:
        raise DomainError(404, "Person not found")
    query = db.query(KeenAgent).filter_by(person_id=person_id, agent_type="workstation")
    total = query.count()
    agents = (
        query.order_by(KeenAgent.name, KeenAgent.id)
        .offset(offset)
        .limit(PAGE_SIZE)
        .all()
    )
    evidence = (
        db.query(AgentPostureObservation, Event)
        .join(Event, Event.id == AgentPostureObservation.event_id)
        .filter(AgentPostureObservation.agent_id.in_([a.id for a in agents]))
        .all()
        if agents
        else []
    )
    grouped = {}
    for pointer, event in evidence:
        grouped.setdefault(pointer.agent_id, []).append(event)
    now = utc_now_naive()
    devices = []
    for agent in agents:
        active = agent.enabled and agent.expires_at > now
        checks = []
        for key, label in CHECKS.items():
            rows = []
            for event in grouped.get(agent.id, []):
                f = observation(event)
                if f is None or f["check"] != key:
                    continue
                fresh = now - MAX_AGE <= event.timestamp <= now
                status = evaluate(f) if fresh and active else "stale"
                rows.append(
                    {
                        "provider": f["provider"],
                        "scope": f["scope"],
                        "status": status,
                        "reason": f.get("reason", ""),
                        "observed_at": _iso(event.timestamp),
                        "event_id": str(event.id),
                    }
                )
            states = {r["status"] for r in rows}
            # Conservative across scopes: one unencrypted mount or unlocked session
            # cannot be hidden by another passing observation.
            status = next(
                (
                    s
                    for s in ("attention", "stale", "unknown", "external", "met")
                    if s in states
                ),
                "unknown",
            )
            if (
                key in ("antivirus", "firewall", "automatic_updates")
                and "met" in states
                and "attention" not in states
            ):
                # Supporting probes (e.g. signature database metadata) do not veto
                # a current positive observation of a protection provider.
                status = "met"
            if agent.health.get("posture_overflow"):
                status = "unknown"
            checks.append(
                {
                    "check": key,
                    "label": label,
                    "status": status,
                    "observations": sorted(
                        rows, key=lambda r: (r["provider"], r["scope"])
                    ),
                }
            )
        devices.append(
            {
                "agent_id": agent.id,
                "hostname": agent.name,
                "active": active,
                "last_seen": _iso(agent.last_seen),
                "overflow": bool(agent.health.get("posture_overflow")),
                "met": sum(c["status"] == "met" for c in checks),
                "target": len(CHECKS),
                "checks": checks,
            }
        )
    return {
        "devices": devices,
        "total": total,
        "offset": offset,
        "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < total else None,
        "freshness_hours": 24,
    }
