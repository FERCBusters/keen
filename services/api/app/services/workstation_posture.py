"""Bounded, evidence-backed workstation summaries for the People register.

Workstation agents send UEU observations: measured states with typed facts.
The rules below turn facts into a result per check. They are versioned so a
result can always be traced to the rules that produced it. Where a check
depends on the organisation's own policy (how soon a screen must lock, how
old antivirus signatures may be), the rule reads the target from an ISMS
effectiveness measure with a fixed metric key; with no such measure, the
check judges only whether the protection is configured.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from pydantic import Field

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentPostureObservation,
    Event,
    IsmsEffectivenessMeasure,
    IsmsPerson,
    IsmsPersonAsset,
    KeenAgent,
)
from app.security.permissions import has_permission
from app.services import posture_risks
from app.services.errors import DomainError
from app.services.features import state as feature_state
from app.services.frameworks import framework_parameter
from app.services.isms.metric_values import threshold_status
from app.services.ueu_evidence import PLATFORMS

RULES_VERSION = "keen-workstation-posture-2"
CHECKS = {
    "disk_encryption": "Disk encryption",
    "screen_lock": "Screen lock",
    "antivirus": "Antivirus",
    "firewall": "Firewall",
    "automatic_updates": "Automatic updates",
}
# ISMS effectiveness measures with these metric keys set an organisation's
# own posture targets. Units must match for a target to apply.
THRESHOLDS = {
    "keen.workstation.screen_lock_seconds": ("screen_lock", "seconds"),
    "keen.workstation.antivirus_signature_age_days": ("antivirus", "days"),
}
COVERAGE = "evidence.collection.completed"
MAX_STREAMS = 128
PAGE_SIZE = 20
MAX_AGE = timedelta(hours=24)
# Workstation assets KEEN creates when a posture risk is recorded.
ASSET_NAMESPACE = uuid.UUID("2c4f0d64-9a59-4c1b-9d0e-6a3b7f1e5c21")


def _envelope(event):
    payload = event.normalized_payload or {}
    doc = payload.get("ueu")
    if not isinstance(doc, dict) or not isinstance(doc.get("event_type"), str):
        return None
    return doc


def observation(event):
    """The posture observation an event holds, or None."""
    doc = _envelope(event)
    if doc is None:
        return None
    parts = doc["event_type"].split(".")
    observed = doc.get("observation")
    if (
        len(parts) != 3
        or parts[0] != "endpoint"
        or parts[2] != "observed"
        or parts[1] not in CHECKS
        or doc.get("catalogue") != {"id": "keen", "version": "2026.10"}
        or not isinstance(observed, dict)
        or not isinstance(observed.get("facts"), dict)
    ):
        return None
    subject = doc.get("subject", {}).get("identifier", "")
    scope = str(observed.get("scope", ""))
    return {
        "check": parts[1],
        "provider": str(doc.get("source", {}).get("component", "")),
        "scope": scope + (" · " + subject[12:24] if subject else ""),
        "stream": [parts[1], doc.get("source", {}).get("component"), scope, subject],
        "reason": str(doc.get("event", {}).get("reason_code", "")),
        "facts": observed["facts"],
    }


def coverage(event):
    doc = _envelope(event)
    if doc is None or doc["event_type"] != COVERAGE:
        return None
    attrs = doc.get("attributes", {})
    if not isinstance(attrs, dict):
        return None
    lists = {}
    for key in ("checks_attempted", "checks_observed", "checks_unavailable"):
        value = attrs.get(key, [])
        lists[key] = (
            [v for v in value if v in CHECKS] if isinstance(value, list) else []
        )
    return {"collector": str(attrs.get("collector", "")), **lists}


def _pointer(db, agent, event, key):
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


def record_observation(db, agent, event_id):
    """Caller holds the authenticated agent row lock and owns the transaction."""
    event = db.get(Event, event_id)
    if agent.posture_assigned_at is None or event.timestamp < agent.posture_assigned_at:
        return
    found = observation(event)
    if found is not None:
        key = hashlib.sha256(json.dumps(found["stream"]).encode()).hexdigest()
        _pointer(db, agent, event, key)
        return
    run = coverage(event)
    if run is not None:
        key = hashlib.sha256(json.dumps(["coverage", run["collector"]]).encode())
        _pointer(db, agent, event, key.hexdigest())


def _all_true(f, *keys):
    values = [f[k] for k in keys if isinstance(f.get(k), bool)]
    if False in values:
        return "attention"
    return "met" if values else "unknown"


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**32


def lock_seconds(f):
    """Seconds of inactivity before the screen locks, where it is known."""
    for key in ("lock_after_seconds", "total_idle_to_lock_seconds"):
        if _positive(f.get(key)):
            return f[key]
    idle = f.get("idle_seconds")
    if _positive(idle):
        delay = f.get("password_delay_seconds", 0)
        return (
            idle + delay if isinstance(delay, int) and 0 <= delay < 2**31 - 1 else None
        )
    return None


def _target(value, threshold, describe):
    """Apply an organisation's target to a configured protection."""
    if threshold is None:
        return "met", ""
    target, operator, unit = threshold
    if value is None:
        return "unknown", "No measured value for the organisation's target"
    result = threshold_status(value, target, operator, unit, unit)
    text = describe(value) + "; target " + operator + " " + f"{target:g}" + " " + unit
    if result == "met":
        return "met", text
    if result == "outside":
        return "attention", text
    return "unknown", text


def evaluate(o, thresholds=None):
    """Return (status, detail) for one observation under RULES_VERSION."""
    thresholds = thresholds or {}
    f, provider, check = o["facts"], o["provider"], o["check"]
    if o["reason"] == "managed_outside_qube":
        return "external", ""
    if check == "disk_encryption":
        if f.get("transition_in_progress") is True:
            return "attention", "Encryption is changing"
        if provider == "bitlocker":
            keys = ("fully_encrypted", "protection_enabled")
            if any(not isinstance(f.get(k), bool) for k in keys):
                return "unknown", ""
            return _all_true(f, *keys), ""
        return _all_true(f, "encrypted", "enabled"), ""
    if check == "screen_lock":
        configured = f.get("automatic_lock_configured")
        if configured is False:
            return "attention", "Automatic locking is not configured"
        if configured is not True:
            if provider == "windows_user_policy":
                # Screen saver values are one of several Windows lock paths, and
                # ScreenSaveActive defaults to 1 with no screen saver selected.
                # Only the combined automatic_lock_configured fact is a result.
                return "unknown", ""
            if (
                f.get("password_required") is False
                or f.get("saver_enabled") is False
                or f.get("idle_seconds") == 0
                or f.get("password_delay_seconds") == 2147483647
            ):
                return "attention", "Locking or the password prompt is off"
            if not (
                f.get("password_required") is True and _positive(f.get("idle_seconds"))
            ):
                return "unknown", ""
        return _target(
            lock_seconds(f),
            thresholds.get("screen_lock"),
            lambda v: f"Locks after {v} s",
        )
    if check == "antivirus":
        state = _all_true(
            f,
            "antivirus_enabled",
            "real_time_protection_enabled",
            "service_enabled",
            "healthy",
        )
        if state == "unknown" and (
            o["reason"]
            in ("components_detected", "service_detected", "product_registered")
            or f.get("installed") is True
        ):
            state = "met"
        if state != "met":
            return state, ""
        age = f.get("signatures_age_days")
        if "antivirus" not in thresholds or not isinstance(age, int):
            return "met", ""
        return _target(
            age, thresholds["antivirus"], lambda v: f"Signatures {v} days old"
        )
    if check == "firewall":
        state = _all_true(f, "enabled", "manager_active")
        if state != "unknown":
            return state, ""
        if f.get("input_policy") in ("DROP", "REJECT") or _positive(
            f.get("input_drop_policy_chains")
        ):
            return "met", ""
        if f.get("input_policy") == "ACCEPT" and f.get("input_rule_count") == 0:
            return "attention", "Inbound traffic is accepted without rules"
        return "unknown", ""
    if check == "automatic_updates":
        if f.get("configuration_effective") == "unknown":
            return "unknown", ""
        if f.get("scheduler_state") in ("inactive", "failed", "not_loaded") or f.get(
            "policy_no_auto_update"
        ) in ("1", 1, True):
            return "attention", "Automatic updates are disabled"
        return _all_true(
            f,
            "automatic_install_configured",
            "automatic_os_install_configured",
            "legacy_scheduled_install_configured",
        ), ""
    return "unknown", ""


def thresholds(db):
    """The organisation's posture targets from ISMS effectiveness measures."""
    out = {}
    rows = db.query(IsmsEffectivenessMeasure).filter(
        IsmsEffectivenessMeasure.metric_key.in_(list(THRESHOLDS))
    )
    for row in rows:
        check, unit = THRESHOLDS[row.metric_key]
        if (
            row.target_value is not None
            and row.threshold_operator in ("lt", "lte", "eq", "gte", "gt")
            and (row.target_unit or "").strip() == unit
        ):
            out[check] = (row.target_value, row.threshold_operator, unit)
    return out


def _platform(agent, events):
    """The device's platform: recorded at ingestion, or from loaded evidence."""
    platform = (agent.health or {}).get("platform")
    if platform in PLATFORMS:
        return platform
    for event in events:
        envelope = (event.normalized_payload or {}).get("ueu") or {}
        platform = (envelope.get("asset") or {}).get("platform")
        if platform in PLATFORMS:
            return platform
    return None


def _iso(value):
    return value.isoformat() + "Z" if value is not None else None


def person_posture(*, db, user, person_id, offset=0, include_revoked=True):
    # Posture is derived evidence; People access alone never discloses it.
    from app.services.isms.access import can_read_isms

    if not can_read_isms(db, user) or not has_permission(db, user, "events.read"):
        raise DomainError(403, "ISMS read access and events.read are required")
    feature_state(db).require("people", "evidence")
    if offset < 0 or offset > 100000:
        raise DomainError(400, "Invalid device offset")
    if db.get(IsmsPerson, person_id) is None:
        raise DomainError(404, "Person not found")
    query = db.query(KeenAgent).filter_by(person_id=person_id, agent_type="workstation")
    # A revoked agent keeps its evidence; a reader can leave it out of the list.
    revoked_total = query.filter(KeenAgent.enabled.is_(False)).count()
    if not include_revoked:
        query = query.filter(KeenAgent.enabled.is_(True))
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
    targets = thresholds(db)
    now = utc_now_naive()
    # Recorded risks show only where risk management is enabled; their titles
    # need risk read access, their effect on a result does not.
    risks_on = feature_state(db).enabled("risk_cia")
    links = (
        posture_risks.linked(
            db,
            "workstation",
            [a.id for a in agents],
            titles=posture_risks.can_read(db, user),
        )
        if risks_on
        else {}
    )
    devices = []
    for agent in agents:
        active = agent.enabled and agent.expires_at > now
        unavailable = set()
        for event in grouped.get(agent.id, []):
            run = coverage(event)
            if run is not None and now - MAX_AGE <= event.timestamp <= now:
                unavailable.update(run["checks_unavailable"])
        checks = []
        for key, label in CHECKS.items():
            rows = []
            for event in grouped.get(agent.id, []):
                o = observation(event)
                if o is None or o["check"] != key:
                    continue
                fresh = now - MAX_AGE <= event.timestamp <= now
                status, detail = (
                    evaluate(o, targets) if fresh and active else ("stale", "")
                )
                rows.append(
                    {
                        "provider": o["provider"],
                        "scope": o["scope"],
                        "status": status,
                        "reason": o["reason"],
                        "detail": detail,
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
                "not_collected" if key in unavailable else "unknown",
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
            risks = links.get((agent.id, "", key, ""), [])
            if status == "attention" and any(r["accepted"] for r in risks):
                status = "accepted"
            checks.append(
                {
                    "check": key,
                    "label": label,
                    "status": status,
                    "observations": sorted(
                        rows, key=lambda r: (r["provider"], r["scope"])
                    ),
                    "risks": risks,
                }
            )
        devices.append(
            {
                "agent_id": agent.id,
                "hostname": agent.name,
                "platform": _platform(agent, grouped.get(agent.id, [])),
                "active": active,
                "revoked": not agent.enabled,
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
        "revoked_total": revoked_total,
        "include_revoked": include_revoked,
        "offset": offset,
        "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < total else None,
        "freshness_hours": 24,
        "rules_version": RULES_VERSION,
        "can_create_risk": risks_on and posture_risks.can_record(db, user),
        "targets": {
            check: {"operator": op, "value": value, "unit": unit}
            for check, (value, op, unit) in targets.items()
        },
    }


class WorkstationRiskInput(posture_risks.PostureRiskInput):
    agent_id: str = Field(min_length=1, max_length=36)
    check: str = Field(min_length=1, max_length=64)


def _workstation_asset(db, agent, person):
    asset = posture_risks.category_asset(
        db,
        asset_id=uuid.uuid5(ASSET_NAMESPACE, "workstation-agent:" + agent.id),
        name=agent.name + " (workstation)",
        category="End-user devices",
        subcategory="Workstations",
        description=(
            "Workstation enrolled as KEEN Agent identity "
            + agent.id
            + ". KEEN created this asset when a posture risk was recorded; "
            "edit it here."
        ),
    )
    if db.get(IsmsPersonAsset, (person.id, asset.id)) is None:
        db.add(IsmsPersonAsset(person_id=person.id, asset_id=asset.id))
    return asset


def create_workstation_risk(*, db, user, person_id, body: WorkstationRiskInput):
    """Record a risk for one of a person's workstation checks."""
    from app.services.isms.access import can_read_isms

    if not can_read_isms(db, user) or not has_permission(db, user, "events.read"):
        raise DomainError(403, "ISMS read access and events.read are required")
    if not posture_risks.can_record(db, user):
        raise DomainError(403, "risk.manage is required to record a risk")
    feature_state(db).require("people", "evidence", "risk_cia")
    if body.check not in CHECKS:
        raise DomainError(400, "Unknown posture check")
    person = db.get(IsmsPerson, person_id)
    if person is None:
        raise DomainError(404, "Person not found")
    agent = db.get(KeenAgent, body.agent_id)
    if (
        agent is None
        or agent.person_id != person.id
        or agent.agent_type != "workstation"
    ):
        raise DomainError(404, "Workstation not found")
    evidence = [
        event.id
        for _, event in db.query(AgentPostureObservation, Event)
        .join(Event, Event.id == AgentPostureObservation.event_id)
        .filter(AgentPostureObservation.agent_id == agent.id)
        .all()
        if (observation(event) or {}).get("check") == body.check
    ]
    framework = framework_parameter(body.framework)
    asset = _workstation_asset(db, agent, person)
    risk = posture_risks.record(
        db,
        user,
        body,
        origin="workstation",
        origin_key=agent.id,
        scope_key="",
        check_key=body.check,
        asset_id=asset.id,
        controls=posture_risks.mapped_controls(db, evidence, framework),
        framework=framework,
        request_path=f"/v1/isms/people/{person_id}/workstation-posture/risks",
    )
    db.commit()
    return {"risk_id": str(risk.id), "asset_id": str(asset.id)}
