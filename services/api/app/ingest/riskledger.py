"""Risk Ledger alpha API pull adapter.

Only documented read operations: supplier search uses POST, all other calls GET.
Assessment answers and attachment downloads are not exposed by this API.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from app.db.models import (
    ControlItem,
    Framework,
    FrameworkClause,
    IngestionCursor,
    IsmsVendor,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
)
from app.ingest.common import store_event_with_artifact
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.http import client as ingestion_client
from app.security.redaction import mask_event_data_obj, redact_obj
from app.services.ingestion_pause import pausable

BASE = "https://api.riskledger.com/alpha"
FRAMEWORK = "RISKLEDGER:ASSESSMENT"
NAMESPACE = uuid.UUID("5263aecb-5782-4716-b234-47a4d266067b")


class RiskLedgerError(ValueError):
    """Operator-safe message; never include API responses or credentials."""


def _id(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise RiskLedgerError("Risk Ledger requires a valid UUID identifier") from None


def validate_config(config):
    if not isinstance(config, dict):
        raise RiskLedgerError("Risk Ledger settings must be an object")
    entries = config.get("organizations", [])
    if not isinstance(entries, list) or len(entries) > 1:
        raise RiskLedgerError("Configure one Risk Ledger organisation per API key")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("org"), str):
            raise RiskLedgerError("Set org to * or the authenticated organisation UUID")
        if entry["org"] != "*":
            _id(entry["org"])
        if (
            not isinstance(entry.get("label", ""), str)
            or len(entry.get("label", "")) > 128
        ):
            raise RiskLedgerError("Risk Ledger label must be at most 128 characters")
    for field in ("control_ids", "domain_ids", "framework_level_ids"):
        values = config.get(field, [])
        if not isinstance(values, list) or len(values) > 500:
            raise RiskLedgerError(f"{field} must be a list of up to 500 UUIDs")
        for value in values:
            _id(value)
        if len(set(values)) != len(values):
            raise RiskLedgerError(f"{field} must contain distinct UUIDs")
    for field in ("import_suppliers", "import_risks", "import_framework"):
        if type(config.get(field, True)) is not bool:
            raise RiskLedgerError(f"{field} must be true or false")
    for field, default, lo, hi in [
        ("page_size", 100, 1, 100),
        ("max_pages", 100, 1, 1000),
        ("max_run_seconds", 300, 30, 1800),
    ]:
        if (
            type(config.get(field, default)) is not int
            or not lo <= config.get(field, default) <= hi
        ):
            raise RiskLedgerError(f"{field} must be between {lo} and {hi}")
    return config


class Client:
    def __init__(self, client, max_run_seconds=300):
        self.client = client
        self.deadline = time.monotonic() + max_run_seconds

    def request(self, path, *, method="GET", params=None, body=None):
        # Callers construct paths exclusively from fixed segments and validated UUIDs.
        if time.monotonic() >= self.deadline:
            raise RiskLedgerError(
                "Risk Ledger run time limit reached; completed snapshots retained. Increase max_run_seconds or reduce scope."
            )
        try:
            with self.client.stream(
                method, BASE + path, params=params, json=body
            ) as response:
                if response.status_code != 200:
                    raise RiskLedgerError(
                        f"Risk Ledger HTTP {response.status_code}; check API access or rate limits. Retry on a later run."
                    )
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= self.deadline:
                        raise RiskLedgerError(
                            "Risk Ledger run time limit reached; completed snapshots retained"
                        )
                    data.extend(chunk)
                    if len(data) > 8 * 1024 * 1024:
                        raise RiskLedgerError(
                            "Risk Ledger response exceeded the 8 MiB limit"
                        )
            result = json.loads(data)
            if not isinstance(result, dict):
                raise RiskLedgerError("Risk Ledger returned an invalid JSON object")
            return result
        except (httpx.HTTPError, ValueError, UnicodeDecodeError) as exc:
            if isinstance(exc, RiskLedgerError):
                raise
            raise RiskLedgerError(
                "Risk Ledger network, TLS or JSON failure; data retained for retry"
            ) from None

    def one(self, kind, ident):
        ident = _id(ident)
        paths = {
            "supplier": "/suppliers/",
            "control": "/framework/controls/",
            "domain": "/framework/domains/",
        }
        row = self.request(paths[kind] + ident)
        if _id(row.get("id")) != ident:
            raise RiskLedgerError("Risk Ledger returned a mismatched record identifier")
        return row

    def pages(self, kind, config):
        offset, seen = 0, set()
        limit = config.get("page_size", 100)
        for _ in range(config.get("max_pages", 100)):
            page = {"offset": offset, "limit": limit}
            response = self.request(
                "/" + kind,
                method="POST" if kind == "suppliers" else "GET",
                body={"page": page} if kind == "suppliers" else None,
                params=page if kind == "risks" else None,
            )
            rows, meta = response.get(kind), response.get("page")
            if (
                not isinstance(rows, list)
                or len(rows) > limit
                or not isinstance(meta, dict)
            ):
                raise RiskLedgerError("Risk Ledger returned invalid pagination data")
            total = meta.get("total")
            if type(total) is not int or total < 0 or meta.get("offset", 0) != offset:
                raise RiskLedgerError(
                    "Risk Ledger returned an inconsistent page offset/total"
                )
            if not rows and offset < total:
                raise RiskLedgerError(
                    "Risk Ledger pagination stopped before the reported total"
                )
            for row in rows:
                if not isinstance(row, dict):
                    raise RiskLedgerError("Risk Ledger returned an invalid record")
                ident = _id(row.get("id"))
                if ident in seen:
                    raise RiskLedgerError(
                        "Risk Ledger repeated a record across pages; run again after upstream changes settle"
                    )
                seen.add(ident)
                yield row
            offset += len(rows)
            if offset >= total:
                return
        raise RiskLedgerError(
            "Risk Ledger page limit reached; increase max_pages or reduce scope"
        )


def _clean(row):
    row = redact_obj(row)
    if settings.event_data_masking == "true":
        row = mask_event_data_obj(row)
    return row


def _local_id(org, kind, remote):
    return uuid.uuid5(NAMESPACE, f"{org}:{kind}:{remote}")


def _text(row, key, default=""):
    value = row.get(key)
    return value if isinstance(value, str) else default


def _safe_website(value):
    try:
        u = urlsplit(value)
        return (
            value[:2048]
            if u.scheme in ("https", "http")
            and u.hostname
            and not u.username
            and not u.password
            else ""
        )
    except ValueError:
        return ""


def _framework(db):
    row = db.query(Framework).filter_by(slug=FRAMEWORK).one_or_none()
    if row is None:
        row = Framework(
            slug=FRAMEWORK,
            name="Risk Ledger assessment framework (imported subset)",
            description="Controls and domains returned by the Risk Ledger API. Coverage is limited to discovered or configured IDs; the public API does not enumerate every control or provide assessment answers/evidence files.",
            upstream_url="https://riskledger.com/resources/framework",
        )
        db.add(row)
        db.flush()
    return row


def _domain(db, row):
    ident = _id(row["id"])
    ref = "domain:" + ident
    meta = {
        "description": _text(row, "description"),
        "upstream_url": BASE + "/framework/domains/" + ident,
        "riskledger_id": ident,
        "domain_letter": _text(row, "letter"),
        "source": "riskledger",
    }
    node = (
        db.query(ControlItem)
        .filter_by(framework_slug=FRAMEWORK, type="clause", ref=ref)
        .one_or_none()
    )
    if node is None:
        node = ControlItem(
            framework_slug=FRAMEWORK,
            type="clause",
            ref=ref,
            title=(_text(row, "letter") + " — " + _text(row, "name", ident))[:256],
            in_scope=False,
            meta=meta,
        )
        db.add(node)
    if (
        not db.query(FrameworkClause)
        .filter_by(framework_slug=FRAMEWORK, ref=ref)
        .first()
    ):
        db.add(
            FrameworkClause(
                framework_slug=FRAMEWORK, ref=ref, title=node.title, meta=meta
            )
        )
    db.flush()
    return node


def _control(db, row, domain):
    ident = _id(row["id"])
    node = (
        db.query(ControlItem)
        .filter_by(framework_slug=FRAMEWORK, type="control", ref=ident)
        .one_or_none()
    )
    if node is None:
        question = _text(row, "question", ident)
        display = f"{_text(domain, 'letter')} {row.get('number', '')}".strip()
        node = ControlItem(
            framework_slug=FRAMEWORK,
            type="control",
            ref=ident,
            title=(display + " — " + question)[:256],
            in_scope=row.get("deprecated") is not True,
            meta={
                "description": question + "\n\n" + _text(row, "description"),
                "upstream_url": BASE + "/framework/controls/" + ident,
                "riskledger_id": ident,
                "domain_id": _id(row["domainID"]),
                "display_reference": display,
                "source": "riskledger",
            },
        )
        db.add(node)
        db.flush()
        from app.db.models import ControlClauseLink

        clause = (
            db.query(FrameworkClause)
            .filter_by(framework_slug=FRAMEWORK, ref="domain:" + _id(domain["id"]))
            .one()
        )
        db.add(
            ControlClauseLink(
                control_item_id=node.id, clause_id=clause.id, applicability="applicable"
            )
        )
    return node


def _vendor(db, org, row):
    ident = _id(row["id"])
    local = _local_id(org, "supplier", ident)
    vendor = db.get(IsmsVendor, local)
    if vendor is None:
        name = _text(row, "name", ident)[:256]
        if db.query(IsmsVendor.id).filter_by(name=name).first():
            name = name[:210] + " [RL " + str(local) + "]"
        vendor = IsmsVendor(
            id=local,
            name=name,
            description=f"Imported from Risk Ledger. Organisation {org}; supplier {ident}. Subsequent API snapshots retain upstream changes; edit this vendor locally.",
            website=_safe_website(_text(row, "website")),
        )
        db.add(vendor)
        db.flush()
    return vendor


def _asset(db, org, supplier, vendor):
    # Keep imported supplier-service risks separate from unrelated local assets.
    category = (
        db.query(RiskCategory).filter_by(name="Risk Ledger imports").one_or_none()
    )
    if category is None:
        category = RiskCategory(name="Risk Ledger imports")
        db.add(category)
        db.flush()
    sub = (
        db.query(RiskAssetSubcategory)
        .filter_by(category_id=category.id, name="Supplier services")
        .one_or_none()
    )
    if sub is None:
        sub = RiskAssetSubcategory(category_id=category.id, name="Supplier services")
        db.add(sub)
        db.flush()
    local = _local_id(org, "asset", supplier or "organisation")
    asset = db.get(RiskAsset, local)
    if asset is None:
        label = vendor.name if vendor else "Organisation risks"
        # Full stable ID distinguishes equal names across organisations and suppliers.
        asset = RiskAsset(
            id=local,
            name=label[:205] + " [RL " + str(local) + "]",
            category_id=category.id,
            subcategory_id=sub.id,
            vendor_id=vendor.id if vendor else None,
            description=f"Risk Ledger supplier-service context. Organisation {org}; supplier {supplier or 'none'}. Review asset scope locally.",
        )
        db.add(asset)
        db.flush()
    return asset


def _risk(db, org, row, vendor, control):
    ident = _id(row["id"])
    local = _local_id(org, "risk", ident)
    risk = db.get(Risk, local)
    if risk is None:
        asset = _asset(db, org, row.get("supplierID"), vendor)
        # Upstream scales and status enum are not defined as KEEN's 5x5 ratings.
        # Preserve those values for a human review rather than inventing a conversion.
        context = {
            k: row.get(k)
            for k in (
                "id",
                "supplierID",
                "riskType",
                "riskOwner",
                "likelihood",
                "impact",
                "riskScore",
                "status",
                "associatedType",
                "associatedID",
            )
        }
        risk = Risk(
            id=local,
            asset_id=asset.id,
            threat_summary=_text(row, "name") + "\n\n" + _text(row, "description"),
            note="Imported from Risk Ledger; review ownership, score and treatment in KEEN. Original values: "
            + json.dumps(context, ensure_ascii=False),
            mitigator_context={"riskledger": {"organisation_id": org, **context}},
            risk_types=[],
        )
        db.add(risk)
        db.flush()
        if control:
            db.add(
                RiskControlLink(
                    risk_id=risk.id,
                    framework_slug=FRAMEWORK,
                    control_item_id=control.id,
                )
            )
    return risk


def _snapshot(db, org, selector, label, kind, row, fields=None):
    ident = _id(row["id"])
    payload = {
        "riskledger": row,
        "fields": {
            "organisation.id": org,
            "record.kind": kind,
            "record.id": ident,
            **(fields or {}),
        },
    }
    content = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = store_event_with_artifact(
        db,
        timestamp=now,
        source="riskledger",
        system=label or "Risk Ledger",
        actor=None,
        action=kind + ".snapshot",
        outcome="info",
        severity=3,
        summary=f"Risk Ledger {kind}: "
        + _text(row, "name", _text(row, "question", ident))[:300],
        raw_pointer={
            "riskledger": {
                "organization_id": org,
                "section": "organizations",
                "org": selector,
                "kind": kind,
                "id": ident,
            }
        },
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="riskledger_" + kind,
        artifact_bytes=content,
        artifact_content_type="application/json",
        artifact_key=f"riskledger/{org}/{kind}/{ident}/{digest}.json",
        captured_by="riskledger",
        commit=False,
    )
    db.commit()
    return int(not result.get("deduped"))


@contextmanager
def _run_lock(db):
    # Single import across keys prevents races creating the shared framework/category.
    with db.get_bind().connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(1262830937)")
        ).scalar()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(1262830937)"))
                    connection.commit()
                except Exception:
                    connection.invalidate()
                    raise


def _collect(db, client, cfg):
    organization = client.request("/organisations/me")
    org = _id(organization.get("id"))
    selection = cfg["organizations"][0]
    selector = selection["org"]
    if selector != "*" and _id(selector) != org:
        raise RiskLedgerError("Configured organisation does not match this API key")
    label = selection.get("label", "")
    domains = {}
    controls = {}
    vendors = {}
    allowed_suppliers = set()
    created = 0
    examined = 0
    levels = {_id(x) for x in cfg.get("framework_level_ids", [])}

    def domain(ident):
        nonlocal created
        ident = _id(ident)
        if ident not in domains:
            row = _clean(client.one("domain", ident))
            domains[ident] = row
            _framework(db)
            _domain(db, row)
            created += _snapshot(
                db, org, selector, label, "domain", row, {"domain.id": ident}
            )
        return domains[ident]

    def control(ident):
        nonlocal created
        ident = _id(ident)
        if ident not in controls:
            row = _clean(client.one("control", ident))
            d = domain(row.get("domainID"))
            controls[ident] = _control(db, row, d)
            created += _snapshot(
                db,
                org,
                selector,
                label,
                "control",
                row,
                {"control.id": ident, "domain.id": _id(d["id"])},
            )
        return controls[ident]

    if cfg.get("import_framework", True):
        _framework(db)
        db.commit()
        for ident in cfg.get("domain_ids", []):
            domain(ident)
        for ident in cfg.get("control_ids", []):
            control(ident)
    if cfg.get("import_suppliers", True) or cfg.get("import_risks", True):
        for remote in client.pages("suppliers", cfg):
            row = _clean(remote)
            sid = _id(row.get("id"))
            level = (row.get("framework") or {}).get("level") or {}
            if levels and level.get("id") not in levels:
                continue
            allowed_suppliers.add(sid)
            examined += 1
            if cfg.get("import_suppliers", True):
                vendors[sid] = _vendor(db, org, row)
            if cfg.get("import_framework", True):
                ids = [x.get("domainID") for x in row.get("complianceByDomain") or []]
                ids += [
                    x.get("id")
                    for x in (row.get("framework") or {}).get("addOnDomains") or []
                ]
                for ident in ids:
                    if ident:
                        domain(ident)
            created += _snapshot(
                db,
                org,
                selector,
                label,
                "supplier",
                row,
                {"supplier.id": sid, "framework.level.id": level.get("id")},
            )
    if cfg.get("import_risks", True):
        for remote in client.pages("risks", cfg):
            row = _clean(remote)
            sid = row.get("supplierID")
            if sid:
                sid = _id(sid)
            if levels and sid not in allowed_suppliers:
                continue
            examined += 1
            ci = None
            if (
                row.get("associatedType") == "control"
                and row.get("associatedID")
                and cfg.get("import_framework", True)
            ):
                ci = control(row["associatedID"])
            # A supplier can exist only on the risk endpoint (e.g. disconnected).
            # Do not invent a vendor or fetch resources beyond the configured scope.
            _risk(db, org, row, vendors.get(sid), ci)
            created += _snapshot(
                db,
                org,
                selector,
                label,
                "risk",
                row,
                {
                    "supplier.id": sid,
                    "control.id": row.get("associatedID")
                    if row.get("associatedType") == "control"
                    else None,
                },
            )
    name = namespace("riskledger:" + org)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
        db.add(cursor)
    cursor.last_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    cursor.meta = {
        "last_success": cursor.last_ts.isoformat(),
        "examined": examined,
        "created_events": created,
        "framework_controls": len(controls),
        "framework_domains": len(domains),
    }
    db.commit()
    return {
        "organization_id": org,
        "examined": examined,
        "created_events": created,
        "controls_discovered": len(controls),
        "domains_discovered": len(domains),
        "coverage": "API snapshots; assessment answers/evidence files and complete control enumeration are not exposed by the documented API",
    }


@pausable("riskledger")
@connection_runs("riskledger")
def ingest_riskledger_all(db):
    if settings.demo_mode or not settings.riskledger_enabled:
        return [{"skipped": True, "reason": "Risk Ledger disabled (or demo mode)"}]
    if not settings.riskledger_api_key.strip():
        return [{"error": "Set KEEN_RISKLEDGER_API_KEY on the API and worker"}]
    try:
        cfg = validate_config(
            load_document("riskledger", settings.riskledger_config_path, db=db)
        )
        if not cfg.get("organizations"):
            return [
                {
                    "skipped": True,
                    "reason": "Add a Risk Ledger organization collection; org * selects the authenticated organisation",
                }
            ]
        with _run_lock(db) as acquired:
            if not acquired:
                return [
                    {
                        "skipped": True,
                        "reason": "Another Risk Ledger import is in progress",
                    }
                ]
            with ingestion_client(
                timeout=httpx.Timeout(30, connect=10),
                verify=True,
                follow_redirects=False,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + settings.riskledger_api_key.strip(),
                    "Accept": "application/json",
                },
            ) as http:
                return [
                    _collect(db, Client(http, cfg.get("max_run_seconds", 300)), cfg)
                ]
    except Exception as exc:
        db.rollback()
        return [
            {
                "error": str(exc)
                if isinstance(exc, RiskLedgerError)
                else "Risk Ledger import failed; check configuration, database and storage. Completed snapshots are retained; rerun safely.",
                "partial": True,
                "retry": "Next scheduled or manual run; no immediate retry loop",
            }
        ]
