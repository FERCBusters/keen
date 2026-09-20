#!/usr/bin/env python3
"""Seed a KEEN instance with deterministic demonstration ISMS data.

Intended location:
    services/api/scripts/seed_demo_data.py

Typical use from the project root:
    docker compose exec api python scripts/seed_demo_data.py

Or, from inside the API container's services/api working directory:
    python scripts/seed_demo_data.py --framework ISO27001:2022 --prefix Demo

The script writes directly through KEEN's SQLAlchemy models. It is deliberately
idempotent for the demo records it creates: re-running it updates matching rows
instead of creating a fresh copy, provided the same --prefix is used.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

# Make the script tolerant of being copied into services/api/scripts/ and run
# as `python scripts/seed_demo_data.py` from services/api. It also supports
# running a downloaded copy from inside the services/api working directory.
SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
for candidate in (API_ROOT, Path.cwd()):
    if (candidate / "app" / "db" / "models.py").exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

from app.core.config import settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    ControlItem,
    Event,
    FrameworkClause,
    InterestedParty,
    InterestedPartyCommunication,
    InterestedPartyControlLink,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsAccessControlMatrixAwsAccount,
    IsmsAccessControlMatrixEntry,
    IsmsAccessControlMatrixRole,
    IsmsAwsAccount,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityControlLink,
    IsmsLicense,
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingLink,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    Mapping,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    User,
)
from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


@dataclass(frozen=True)
class DemoConfig:
    framework: str
    prefix: str
    username: str | None
    dry_run: bool


class DemoSeeder:
    def __init__(self, db: Session, config: DemoConfig) -> None:
        self.db = db
        self.config = config
        self.stats: Counter[str] = Counter()
        self.warnings: dict[str, set[str]] = defaultdict(set)
        self.user = self._select_user(config.username)
        self.relevance_levels: dict[str, PestleRelevanceLevel] = {}

    @property
    def prefix(self) -> str:
        return self.config.prefix.strip() or "Demo"

    @property
    def framework(self) -> str:
        return self.config.framework.strip() or settings.default_framework_slug

    def label(self, suffix: str) -> str:
        return f"{self.prefix} {suffix}".strip()

    def account_code(self, suffix: str) -> str:
        base = re.sub(r"[^A-Z0-9]", "", self.prefix.upper()) or "DEMO"
        suffix = re.sub(r"[^A-Z0-9-]", "", suffix.upper()) or "GEN"
        return f"{base[:12]}-{suffix}"[:32]

    def _select_user(self, username: str | None) -> User:
        query = self.db.query(User).filter(User.is_active.is_(True))
        if username:
            user = query.filter(User.username == username).first()
            if not user:
                raise SystemExit(
                    f"No active KEEN user found with username {username!r}"
                )
            return user
        user = (
            query.filter(User.role == "admin").order_by(User.created_at.asc()).first()
        )
        if not user:
            user = query.order_by(User.created_at.asc()).first()
        if not user:
            raise SystemExit(
                "No active KEEN user exists yet. Create/login an admin user first, "
                "or pass --username for the user that should own the demo records."
            )
        return user

    def upsert(
        self,
        model: type,
        filters: dict[str, Any],
        values: dict[str, Any] | None = None,
        *,
        label: str | None = None,
    ) -> Any:
        values = dict(values or {})
        row = self.db.query(model).filter_by(**filters).first()
        stat_name = label or getattr(model, "__tablename__", model.__name__)
        if row is None:
            row = model(**filters, **values)
            self.db.add(row)
            self.db.flush()
            self.stats[f"{stat_name}.created"] += 1
            return row

        changed = False
        for key, value in values.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed = True
        if changed:
            self.db.flush()
            self.stats[f"{stat_name}.updated"] += 1
        else:
            self.stats[f"{stat_name}.unchanged"] += 1
        return row

    def ensure_link(
        self,
        model: type,
        filters: dict[str, Any],
        values: dict[str, Any] | None = None,
    ) -> Any:
        row = self.db.query(model).filter_by(**filters).first()
        stat_name = getattr(model, "__tablename__", model.__name__)
        if row is not None:
            self.stats[f"{stat_name}.unchanged"] += 1
            return row
        row = model(**filters, **dict(values or {}))
        self.db.add(row)
        self.db.flush()
        self.stats[f"{stat_name}.created"] += 1
        return row

    def _control(self, ref: str) -> ControlItem | None:
        variants = {ref, ref.strip()}
        if ref.startswith("A."):
            variants.add(ref[2:])
        elif re.match(r"^\d+\.\d+", ref):
            variants.add(f"A.{ref}")
        for candidate in variants:
            row = (
                self.db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == self.framework,
                    ControlItem.ref == candidate,
                )
                .first()
            )
            if row:
                return row
        self.warnings["missing controls"].add(ref)
        return None

    def _clause(self, ref: str) -> FrameworkClause | None:
        row = (
            self.db.query(FrameworkClause)
            .filter(
                FrameworkClause.framework_slug == self.framework,
                FrameworkClause.ref == ref,
            )
            .first()
        )
        if not row:
            self.warnings["missing clauses"].add(ref)
        return row

    def link_risk_controls(self, risk: Risk, refs: Iterable[str]) -> None:
        for ref in refs:
            control = self._control(ref)
            if not control:
                continue
            self.ensure_link(
                RiskControlLink,
                {
                    "risk_id": risk.id,
                    "framework_slug": self.framework,
                    "control_item_id": control.id,
                },
            )

    def link_interested_party_controls(
        self, party: InterestedParty, refs: Iterable[str]
    ) -> None:
        for ref in refs:
            control = self._control(ref)
            if not control:
                continue
            self.ensure_link(
                InterestedPartyControlLink,
                {
                    "interested_party_id": party.id,
                    "framework_slug": self.framework,
                    "control_item_id": control.id,
                },
            )

    def link_isms_controls(
        self, entity_type: str, entity_id: Any, refs: Iterable[str]
    ) -> None:
        for ref in refs:
            control = self._control(ref)
            if not control:
                continue
            self.ensure_link(
                IsmsEntityControlLink,
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "framework_slug": self.framework,
                    "control_item_id": control.id,
                },
                {"created_by_user_id": self.user.id},
            )

    def ensure_relevance_levels(self) -> None:
        rows = [
            ("na", "N/A", 0),
            ("high", "1 High", 1),
            ("medium", "2 Medium", 2),
            ("low", "3 Low", 3),
        ]
        for code, label, sort_order in rows:
            self.relevance_levels[code] = self.upsert(
                PestleRelevanceLevel,
                {"code": code},
                {"label": label, "sort_order": sort_order},
            )

    def seed_org_nodes(self) -> dict[str, IsmsOrgNode]:
        board = self.upsert(
            IsmsOrgNode,
            {"name": self.label("Board")},
            {
                "node_type": "team",
                "description": "Demo governance group for ISMS oversight.",
                "sort_order": 10,
                "created_by_user_id": self.user.id,
            },
        )
        roles: dict[str, IsmsOrgNode] = {"board": board}
        role_specs = [
            ("isms_owner", "ISMS Owner", "Accountable owner for the ISMS."),
            ("security_lead", "Security Lead", "Coordinates security controls."),
            ("platform_engineer", "Platform Engineer", "Operates hosting platforms."),
            ("service_desk", "Service Desk", "First-line operational support."),
            ("dpo", "Data Protection Officer", "Privacy and notification lead."),
        ]
        for index, (key, suffix, description) in enumerate(role_specs, start=1):
            roles[key] = self.upsert(
                IsmsOrgNode,
                {"name": self.label(suffix)},
                {
                    "parent_id": board.id,
                    "node_type": "role",
                    "description": description,
                    "sort_order": 10 + index,
                    "created_by_user_id": self.user.id,
                },
            )
        return roles

    def seed_licenses(self) -> dict[str, IsmsLicense]:
        specs = {
            "internal": (
                "Internal Proprietary Service",
                "Owned and maintained by the organisation for demonstration purposes.",
            ),
            "mit": (
                "MIT Licensed Components",
                "Open source components under permissive MIT-style licensing.",
            ),
            "commercial": (
                "Commercial SaaS Subscription",
                "Externally hosted managed service with supplier terms.",
            ),
        }
        return {
            key: self.upsert(
                IsmsLicense,
                {"name": self.label(name)},
                {"description": description, "created_by_user_id": self.user.id},
            )
            for key, (name, description) in specs.items()
        }

    def seed_assets(
        self, licenses: dict[str, IsmsLicense], org_nodes: dict[str, IsmsOrgNode]
    ) -> dict[str, RiskAsset]:
        category_specs = {
            "hosting": "Hosting Platform",
            "data": "Information Asset",
            "application": "Application Service",
            "people": "People and Operations",
        }
        categories = {
            key: self.upsert(RiskCategory, {"name": self.label(name)})
            for key, name in category_specs.items()
        }
        subcategory_specs = {
            "cloud": ("hosting", "Managed Cloud Account"),
            "customer_data": ("data", "Customer Data Store"),
            "web_app": ("application", "Web Application"),
            "ops_team": ("people", "Operations Team"),
        }
        subcategories = {
            key: self.upsert(
                RiskAssetSubcategory,
                {"category_id": categories[cat_key].id, "name": self.label(name)},
            )
            for key, (cat_key, name) in subcategory_specs.items()
        }
        asset_specs = {
            "platform": {
                "name": "Production Platform",
                "category": "hosting",
                "subcategory": "cloud",
                "license": "commercial",
                "owner": "platform_engineer",
                "description": "Primary managed hosting environment used by the demo service.",
            },
            "data_store": {
                "name": "Customer Data Store",
                "category": "data",
                "subcategory": "customer_data",
                "license": "internal",
                "owner": "dpo",
                "description": "Representative customer and audit evidence data store.",
            },
            "web_app": {
                "name": "KEEN Demo Web App",
                "category": "application",
                "subcategory": "web_app",
                "license": "mit",
                "owner": "security_lead",
                "description": "Demo-facing KEEN application and static frontend.",
            },
            "service_desk": {
                "name": "Service Desk Team",
                "category": "people",
                "subcategory": "ops_team",
                "license": "internal",
                "owner": "service_desk",
                "description": "Operational team that triages alerts, requests, and incidents.",
            },
        }
        assets: dict[str, RiskAsset] = {}
        for key, spec in asset_specs.items():
            category = categories[spec["category"]]
            subcategory = subcategories[spec["subcategory"]]
            license_obj = licenses[spec["license"]]
            owner = org_nodes[spec["owner"]]
            assets[key] = self.upsert(
                RiskAsset,
                {
                    "name": self.label(spec["name"]),
                    "category_id": category.id,
                    "subcategory_id": subcategory.id,
                },
                {
                    "license": license_obj.name,
                    "license_id": license_obj.id,
                    "owner_org_node_id": owner.id,
                    "register_held_by_org_node_id": org_nodes["isms_owner"].id,
                    "description": spec["description"],
                    "created_by_user_id": self.user.id,
                },
            )
        return assets

    def seed_hosting_accounts(self) -> dict[str, IsmsAwsAccount]:
        specs = {
            "prod": (
                "Production Hosting Account",
                "PROD-001",
                "Primary production workloads.",
            ),
            "stage": (
                "Staging Hosting Account",
                "STAGE-001",
                "Pre-production validation workloads.",
            ),
            "backup": (
                "Backup Hosting Account",
                "BACKUP-001",
                "Isolated backup and restore environment.",
            ),
        }
        return {
            key: self.upsert(
                IsmsAwsAccount,
                {"account_id": self.account_code(code)},
                {
                    "name": self.label(name),
                    "notes": notes,
                    "created_by_user_id": self.user.id,
                },
            )
            for key, (name, code, notes) in specs.items()
        }

    def seed_business_processes(self) -> dict[str, PestleBusinessProcess]:
        specs = {
            "onboarding": ("Customer onboarding", "New customer setup and assurance."),
            "service": ("Service delivery", "Day-to-day hosting and support."),
            "incident": ("Incident management", "Detection, response, and recovery."),
            "supplier": ("Supplier management", "Supplier risk and contract reviews."),
        }
        pestle_processes: dict[str, PestleBusinessProcess] = {}
        for order, (key, (name, description)) in enumerate(specs.items(), start=10):
            # ISMS business processes drive the application-configuration matrix.
            # PESTLE(E) has its own business-process table, so seed both and pass
            # the PESTLE-specific rows to seed_pestle().
            self.upsert(
                IsmsBusinessProcess,
                {"name": self.label(name)},
                {"description": description, "sort_order": order, "is_default": False},
            )
            pestle_processes[key] = self.upsert(
                PestleBusinessProcess,
                {"name": self.label(name)},
                {"created_by_user_id": self.user.id},
            )
        return pestle_processes

    def seed_risks(self, assets: dict[str, RiskAsset]) -> dict[str, Risk]:
        specs = {
            "admin_access": {
                "asset": "platform",
                "risk_types": ["confidentiality", "integrity", "availability"],
                "summary": "Misconfigured hosting permissions allow unauthorised administrative access.",
                "scores": (4, 4, 5, 20, 2, 3, 6),
                "controls": ["A.5.15", "A.5.16", "A.8.2", "A.8.15"],
            },
            "data_exposure": {
                "asset": "data_store",
                "risk_types": ["confidentiality"],
                "summary": "Customer or audit evidence data is exposed through weak access review or retention controls.",
                "scores": (4, 3, 5, 15, 2, 3, 6),
                "controls": ["A.5.34", "A.8.3", "A.8.12", "A.8.24"],
            },
            "release_defect": {
                "asset": "web_app",
                "risk_types": ["integrity", "availability"],
                "summary": "Release or dependency defect disrupts evidence ingestion or audit workflows.",
                "scores": (3, 3, 4, 12, 2, 2, 4),
                "controls": ["A.8.8", "A.8.9", "A.8.25", "A.8.28"],
            },
            "key_person": {
                "asset": "service_desk",
                "risk_types": ["availability"],
                "summary": "Key operations staff are unavailable during incident response or audit preparation.",
                "scores": (3, 3, 3, 9, 2, 2, 4),
                "controls": ["A.5.3", "A.5.30", "A.6.3", "A.6.5"],
            },
        }
        risks: dict[str, Risk] = {}
        for key, spec in specs.items():
            t, v, i, score, rv, ri, rscore = spec["scores"]
            risk = self.upsert(
                Risk,
                {
                    "asset_id": assets[spec["asset"]].id,
                    "threat_summary": spec["summary"],
                },
                {
                    "risk_types": spec["risk_types"],
                    "risk_owner_user_id": self.user.id,
                    "threat_score": t,
                    "vulnerability_score": v,
                    "impact_score": i,
                    "risk_score": score,
                    "residual_vulnerability_score": rv,
                    "residual_impact_score": ri,
                    "residual_risk_score": rscore,
                    "note": "Seeded demo CIA risk. Adjust ownership/scoring for live use.",
                    "mitigator_context": {"seed": "demo", "prefix": self.prefix},
                    "created_by_user_id": self.user.id,
                },
            )
            self.link_risk_controls(risk, spec["controls"])
            risks[key] = risk
        return risks

    def seed_pestle(self, processes: dict[str, IsmsBusinessProcess]) -> None:
        high = self.relevance_levels["high"]
        medium = self.relevance_levels["medium"]
        specs = [
            (
                "Political",
                "External",
                "Public-sector cyber assurance expectations increase evidence obligations.",
                "New assurance requirements may require clearer control ownership, documented evidence, and audit-ready reporting.",
                ["onboarding", "service"],
                ["4.1", "4.2", "6.1"],
            ),
            (
                "Economical",
                "External",
                "Supplier price rises pressure tooling and monitoring coverage.",
                "Budget changes may reduce monitoring depth unless control effectiveness is clearly measured.",
                ["supplier", "service"],
                ["4.2", "6.1", "8.1"],
            ),
            (
                "Social",
                "External",
                "Customers expect clear privacy notices and rapid incident communication.",
                "Customer trust is affected by the speed and transparency of security communication.",
                ["onboarding", "incident"],
                ["4.2", "7.4", "8.2"],
            ),
            (
                "Technological",
                "Internal",
                "Cloud platform changes introduce configuration drift risk.",
                "Managed hosting changes must be detected and linked to access review, logging, and hardening controls.",
                ["service", "incident"],
                ["6.1", "8.1", "8.3"],
            ),
            (
                "Legal",
                "External",
                "Retention and breach-notification obligations require reliable records.",
                "Evidence must support investigation timelines, legal retention, and notification decisions.",
                ["incident", "supplier"],
                ["4.2", "8.2", "9.1"],
            ),
            (
                "Environmental",
                "External",
                "Regional outage or office disruption affects support operations.",
                "Continuity assumptions should be tested through tabletop and restore exercises.",
                ["service", "incident"],
                ["6.1", "8.4", "8.5"],
            ),
            (
                "Ethical",
                "Internal",
                "AI-assisted administration requires transparency and human review.",
                "Use of assistive tooling should not bypass approval, review, or accountability controls.",
                ["service", "supplier"],
                ["5.1", "5.4", "7.3"],
            ),
        ]
        for typ, lens, item, rationale, process_keys, clause_refs in specs:
            row = self.upsert(
                PestleItem,
                {"framework_slug": self.framework, "item": self.label(item)},
                {
                    "type": typ,
                    "lens": lens,
                    "overall_relevance_id": high.id,
                    "rationale": rationale,
                    "created_by_user_id": self.user.id,
                },
            )
            for key in process_keys:
                self.ensure_link(
                    PestleBusinessProcessRelevance,
                    {
                        "pestle_item_id": row.id,
                        "business_process_id": processes[key].id,
                    },
                    {"relevance_id": medium.id},
                )
            for ref in clause_refs:
                clause = self._clause(ref)
                if not clause:
                    continue
                self.ensure_link(
                    PestleClauseRelevance,
                    {"pestle_item_id": row.id, "clause_id": clause.id},
                    {"relevance_id": high.id},
                )

    def seed_interested_parties(self) -> None:
        specs = [
            {
                "name": "Customers",
                "nature": "Expect secure and available service",
                "note": "Customers require reliable service delivery, clear security obligations, and timely incident communication.",
                "controls": ["A.5.1", "A.5.24", "A.5.26", "A.8.15"],
                "communications": [
                    ("Business as Usual", "Routine", "Individual member", ["Email"]),
                    (
                        "Incident",
                        "Within 24 hours",
                        "Individual member",
                        ["Email", "Telephone"],
                    ),
                ],
            },
            {
                "name": "Staff",
                "nature": "Need clear policies and secure tooling",
                "note": "Staff need current policies, training, and access to approved systems.",
                "controls": ["A.5.4", "A.6.3", "A.6.5", "A.8.2"],
                "communications": [
                    (
                        "Business as Usual",
                        "Routine",
                        "Individual staff member",
                        ["Email", "Chat"],
                    ),
                    (
                        "Alert",
                        "Upon Identification",
                        "Individual staff member",
                        ["Chat"],
                    ),
                ],
            },
            {
                "name": "Regulators",
                "nature": "Expect notification and evidence records",
                "note": "Regulators may require notification records, investigation evidence, and management review outcomes.",
                "controls": ["A.5.31", "A.5.34", "A.5.36", "A.8.15"],
                "communications": [
                    (
                        "Notifiable Event",
                        "Within 72 hours",
                        "ICO",
                        ["ICO Portal", "Email"],
                    ),
                ],
            },
            {
                "name": "Managed hosting provider",
                "nature": "Supplier assurance and technical support",
                "note": "Hosting suppliers support operational resilience and require assurance review.",
                "controls": ["A.5.19", "A.5.20", "A.5.21", "A.5.23"],
                "communications": [
                    (
                        "Incident",
                        "As Required",
                        "Supplier Point of Contact",
                        ["Email", "Telephone"],
                    ),
                    (
                        "Business as Usual",
                        "Routine",
                        "Senior Supplier Point of Contact",
                        ["Email"],
                    ),
                ],
            },
        ]
        for spec in specs:
            name = self.upsert(
                InterestedPartyName,
                {"name": self.label(spec["name"])},
                {"created_by_user_id": self.user.id},
            )
            nature = self.upsert(
                InterestedPartyNature,
                {"name": spec["nature"]},
                {"created_by_user_id": self.user.id},
            )
            party = self.upsert(
                InterestedParty,
                {
                    "framework_slug": self.framework,
                    "name_id": name.id,
                    "nature_id": nature.id,
                },
                {"note": spec["note"], "created_by_user_id": self.user.id},
            )
            self.link_interested_party_controls(party, spec["controls"])
            for event, when, with_whom, methods in spec["communications"]:
                self.ensure_link(
                    InterestedPartyCommunication,
                    {
                        "interested_party_id": party.id,
                        "event": event,
                        "when": when,
                        "with_whom": with_whom,
                    },
                    {"methods": methods},
                )

    def seed_access_matrix(
        self,
        assets: dict[str, RiskAsset],
        accounts: dict[str, IsmsAwsAccount],
        org_nodes: dict[str, IsmsOrgNode],
    ) -> None:
        specs = [
            {
                "task": "Approve production administrator access",
                "asset": "platform",
                "status": "Approved",
                "accounts": ["prod"],
                "roles": ["security_lead", "platform_engineer"],
                "notes": "Production admin access requires ticket approval and quarterly review.",
                "controls": ["A.5.15", "A.5.16", "A.8.2"],
            },
            {
                "task": "Rotate backup restore credentials",
                "asset": "data_store",
                "status": "Pending Approval",
                "accounts": ["backup"],
                "roles": ["platform_engineer"],
                "notes": "Credential rotation is pending the next restore validation window.",
                "controls": ["A.5.17", "A.8.13", "A.8.24"],
            },
            {
                "task": "Deploy application release to staging",
                "asset": "web_app",
                "status": "Approved",
                "accounts": ["stage"],
                "roles": ["security_lead", "platform_engineer"],
                "notes": "Staging deployment is allowed after code review and CI checks pass.",
                "controls": ["A.8.8", "A.8.25", "A.8.28"],
            },
            {
                "task": "Triage customer security request",
                "asset": "service_desk",
                "status": "Approved",
                "accounts": ["prod", "stage"],
                "roles": ["service_desk", "dpo"],
                "notes": "Support staff can inspect metadata but not export evidence without approval.",
                "controls": ["A.5.4", "A.5.24", "A.6.3"],
            },
        ]
        for spec in specs:
            entry = self.upsert(
                IsmsAccessControlMatrixEntry,
                {"task_action": self.label(spec["task"])},
                {
                    "service_asset_id": assets[spec["asset"]].id,
                    "status": spec["status"],
                    "approved_by_user_id": (
                        self.user.id if spec["status"] == "Approved" else None
                    ),
                    "notes": spec["notes"],
                    "created_by_user_id": self.user.id,
                },
            )
            for account_key in spec["accounts"]:
                self.ensure_link(
                    IsmsAccessControlMatrixAwsAccount,
                    {"entry_id": entry.id, "aws_account_id": accounts[account_key].id},
                )
            for role_key in spec["roles"]:
                self.ensure_link(
                    IsmsAccessControlMatrixRole,
                    {"entry_id": entry.id, "org_node_id": org_nodes[role_key].id},
                )
            self.link_isms_controls("access_control_matrix", entry.id, spec["controls"])

    def seed_documents(self) -> dict[str, IsmsDocument]:
        specs = {
            "security_policy": (
                "Information Security Policy",
                "policy",
                "Sets the organisation's ISMS principles, roles, and control expectations.",
                ["A.5.1", "A.5.2", "A.5.4"],
            ),
            "access_procedure": (
                "Access Control Procedure",
                "procedure",
                "Defines account lifecycle, approval, review, and privileged access handling.",
                ["A.5.15", "A.5.16", "A.5.18", "A.8.2"],
            ),
            "incident_process": (
                "Incident Response Process",
                "process",
                "Describes preparation, triage, response, communication, and lessons learned.",
                ["A.5.24", "A.5.25", "A.5.26", "A.5.27"],
            ),
            "supplier_standard": (
                "Supplier Management Standard",
                "standard",
                "Defines supplier due diligence, assurance, contractual security, and review cadence.",
                ["A.5.19", "A.5.20", "A.5.21", "A.5.22"],
            ),
            "secure_dev_guideline": (
                "Secure Development Guideline",
                "guideline",
                "Provides secure coding, review, dependency, and release guidance.",
                ["A.8.25", "A.8.27", "A.8.28", "A.8.29"],
            ),
        }
        docs: dict[str, IsmsDocument] = {}
        for key, (title, doc_type, description, controls) in specs.items():
            doc = self.upsert(
                IsmsDocument,
                {"title": self.label(title)},
                {
                    "document_type": doc_type,
                    "description": description,
                    "external_url": f"https://example.invalid/keen-demo/{key}",
                    "created_by_user_id": self.user.id,
                },
            )
            self.link_isms_controls("document", doc.id, controls)
            docs[key] = doc
        return docs

    def _scope_audit_controls(self, audit: Audit, refs: Iterable[str]) -> None:
        for ref in refs:
            control = self._control(ref)
            if not control:
                continue
            self.ensure_link(
                AuditScopedControl,
                {"audit_id": audit.id, "control_item_id": control.id},
            )

    def _scope_audit_clauses(self, audit: Audit, refs: Iterable[str]) -> None:
        for ref in refs:
            clause = self._clause(ref)
            if not clause:
                continue
            self.ensure_link(
                AuditScopedClause,
                {"audit_id": audit.id, "clause_id": clause.id},
            )

    def _scope_audit_documents(
        self, audit: Audit, documents: Iterable[IsmsDocument]
    ) -> None:
        for document in documents:
            self.ensure_link(
                AuditScopedIsmsDocument,
                {"audit_id": audit.id, "document_id": document.id},
            )

    def _sample_entity_into_audit(
        self,
        audit: Audit,
        entity_type: str,
        entity_id: Any,
        title: str,
        notes: str,
        url: str | None = None,
    ) -> None:
        self.ensure_link(
            AuditEvidence,
            {
                "audit_id": audit.id,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
            {
                "event_id": None,
                "evidence_url": url,
                "title": title[:512],
                "notes": notes,
                "added_by_user_id": self.user.id,
                "meta": {"seed": "demo", "prefix": self.prefix},
            },
        )

    def _sample_event_into_audit(
        self, audit: Audit, event: Event, title: str, notes: str
    ) -> None:
        self.ensure_link(
            AuditEvidence,
            {"audit_id": audit.id, "event_id": event.id},
            {
                "entity_type": None,
                "entity_id": None,
                "evidence_url": f"/event.html?id={event.id}",
                "title": title[:512],
                "notes": notes,
                "added_by_user_id": self.user.id,
                "meta": {"seed": "demo", "prefix": self.prefix},
            },
        )

    def seed_diary_events(self) -> dict[str, Event]:
        specs = {
            "management_review": {
                "summary": "Management review confirms priority control evidence gaps",
                "days_ago": 12,
                "controls": ["A.5.1", "A.5.35", "A.5.36"],
                "details": {
                    "meeting": "Monthly ISMS review",
                    "decision": "Prioritise access review and supplier assurance evidence before the next audit.",
                    "actions": [
                        "Update access review effectiveness measure",
                        "Collect supplier security contact evidence",
                    ],
                },
            },
            "access_review": {
                "summary": "Quarterly privileged access review completed",
                "days_ago": 9,
                "controls": ["A.5.15", "A.5.16", "A.5.18", "A.8.2"],
                "details": {
                    "reviewed_accounts": 17,
                    "removed_accounts": 2,
                    "exceptions": 1,
                    "outcome": "One temporary administrator exception retained with expiry date.",
                },
            },
            "supplier_assurance": {
                "summary": "Managed hosting supplier assurance evidence received",
                "days_ago": 6,
                "controls": ["A.5.19", "A.5.20", "A.5.21", "A.5.23"],
                "details": {
                    "supplier": "Demo managed hosting provider",
                    "evidence": [
                        "SOC summary",
                        "Shared responsibility statement",
                        "Support escalation contact",
                    ],
                    "outcome": "Evidence accepted for demonstration scope.",
                },
            },
            "tabletop": {
                "summary": "Incident tabletop exercise completed",
                "days_ago": 3,
                "controls": ["A.5.24", "A.5.25", "A.5.26", "A.5.27", "A.5.30"],
                "details": {
                    "scenario": "Production hosting outage with customer notification decision",
                    "lessons": [
                        "Clarify incident commander handover",
                        "Add customer update template to incident process",
                    ],
                    "outcome": "Follow-up actions assigned and due next month.",
                },
            },
        }
        events: dict[str, Event] = {}
        for key, spec in specs.items():
            timestamp = datetime.combine(
                date.today() - timedelta(days=int(spec["days_ago"])),
                time(11, 30),
            )
            summary = self.label(spec["summary"])
            external_id = f"{self.account_code('DIARY')}-{key}"[:128]
            payload = {
                "summary": summary,
                "details": spec["details"],
                "author": self.user.username,
                "links": [
                    {
                        "text": "Demo diary evidence link",
                        "url": "https://example.invalid/keen-demo/diary",
                    }
                ],
            }
            event = self.upsert(
                Event,
                {"source": "diary", "external_id": external_id},
                {
                    "timestamp": timestamp,
                    "system": "manual",
                    "actor": self.user.username,
                    "action": "diary_entry",
                    "outcome": "info",
                    "severity": 2,
                    "summary": f"diary: {summary}",
                    "raw_pointer": {"seed": "demo", "prefix": self.prefix},
                    "normalized_payload": payload,
                },
            )
            for ref in spec["controls"]:
                control = self._control(ref)
                if not control:
                    continue
                self.ensure_link(
                    Mapping,
                    {"event_id": event.id, "control_item_id": control.id},
                    {
                        "confidence": 1.0,
                        "method": "manual",
                        "rationale": "seeded demo diary mapping",
                        "mapped_by": self.user.username,
                    },
                )
            events[key] = event
        return events

    def _seed_source_event(
        self,
        *,
        key: str,
        source: str,
        timestamp: datetime,
        system: str | None,
        actor: str | None,
        action: str,
        outcome: str,
        severity: int,
        summary: str,
        raw_pointer: dict[str, Any],
        normalized_payload: dict[str, Any],
        controls: Iterable[str],
    ) -> Event:
        external_id = f"{self.account_code('EVENT')}-{key}"[:128]
        event = self.upsert(
            Event,
            {"source": source, "external_id": external_id},
            {
                "timestamp": timestamp,
                "system": system,
                "actor": actor,
                "action": action,
                "outcome": outcome,
                "severity": severity,
                "summary": self.label(summary),
                "raw_pointer": raw_pointer,
                "normalized_payload": normalized_payload,
            },
        )
        for ref in controls:
            control = self._control(ref)
            if not control:
                continue
            self.ensure_link(
                Mapping,
                {"event_id": event.id, "control_item_id": control.id},
                {
                    "confidence": 0.82,
                    "method": "seeded-demo",
                    "rationale": f"Seeded {source} demonstration event mapped to {ref}",
                    "mapped_by": self.user.username,
                },
            )
        return event

    def _demo_event_timestamp(self, days_ago: int, hour: int, minute: int) -> datetime:
        return datetime.combine(
            date.today() - timedelta(days=days_ago), time(hour, minute)
        )

    def seed_source_events(self) -> dict[str, Event]:
        """Seed non-RSS evidence/events for demo source lenses.

        RSS feed events are intentionally not seeded here: demonstration RSS
        sources can populate themselves through the normal RSS ingester.
        """
        events: dict[str, Event] = {}

        loki_events = [
            {
                "key": "loki-ossec-ssh-bruteforce",
                "days_ago": 5,
                "at": (2, 14),
                "system": "app01.demo.internal",
                "actor": "sshd",
                "action": "auth_failure_threshold",
                "outcome": "failed",
                "severity": 4,
                "summary": "Loki OSSEC app01 SSH authentication failures exceeded threshold",
                "labels": {
                    "job": "varlogs",
                    "host": "app01.demo.internal",
                    "agent": "alloy",
                    "program": "ossec",
                },
                "log": "ossec: Alert level 10, Rule 5712: SSHD brute force trying to get access to the system.",
                "ossec": {
                    "rule_id": "5712",
                    "level": 10,
                    "decoder": "sshd",
                    "srcip": "203.0.113.24",
                    "user": "root",
                    "description": "SSHD brute force trying to get access to the system.",
                },
                "controls": ["A.5.24", "A.8.15", "A.8.16"],
            },
            {
                "key": "loki-ossec-sshd-config-change",
                "days_ago": 4,
                "at": (9, 42),
                "system": "bastion01.demo.internal",
                "actor": "ossec-syscheck",
                "action": "file_integrity_change",
                "outcome": "info",
                "severity": 4,
                "summary": "Loki OSSEC bastion file integrity change for sshd_config",
                "labels": {
                    "job": "varlogs",
                    "host": "bastion01.demo.internal",
                    "agent": "alloy",
                    "program": "ossec",
                },
                "log": "ossec: Integrity checksum changed for '/etc/ssh/sshd_config'.",
                "ossec": {
                    "rule_id": "550",
                    "level": 7,
                    "decoder": "syscheck_integrity_changed",
                    "path": "/etc/ssh/sshd_config",
                    "description": "Integrity checksum changed.",
                },
                "controls": ["A.5.15", "A.8.9", "A.8.32"],
            },
            {
                "key": "loki-ossec-rootcheck-hardening",
                "days_ago": 3,
                "at": (6, 5),
                "system": "web01.demo.internal",
                "actor": "ossec-rootcheck",
                "action": "configuration_assessment",
                "outcome": "warning",
                "severity": 3,
                "summary": "Loki OSSEC web01 rootcheck reports hardening exception",
                "labels": {
                    "job": "varlogs",
                    "host": "web01.demo.internal",
                    "agent": "alloy",
                    "program": "ossec",
                },
                "log": "ossec: Rootcheck found world-writable temporary directory exception under /srv/app/tmp.",
                "ossec": {
                    "rule_id": "510",
                    "level": 5,
                    "decoder": "rootcheck",
                    "path": "/srv/app/tmp",
                    "description": "World writable directory exception reviewed.",
                },
                "controls": ["A.8.8", "A.8.9", "A.8.16"],
            },
            {
                "key": "loki-syslog-sudo-deploy",
                "days_ago": 2,
                "at": (13, 18),
                "system": "app01.demo.internal",
                "actor": "deploy",
                "action": "sudo_command",
                "outcome": "success",
                "severity": 3,
                "summary": "Loki syslog app01 sudo restart of keen-api by deploy",
                "labels": {
                    "job": "varlogs",
                    "host": "app01.demo.internal",
                    "agent": "alloy",
                    "program": "sudo",
                },
                "log": "sudo: deploy : TTY=pts/0 ; PWD=/srv/keen ; USER=root ; COMMAND=/bin/systemctl restart keen-api",
                "syslog": {
                    "facility": "authpriv",
                    "program": "sudo",
                    "command": "/bin/systemctl restart keen-api",
                },
                "controls": ["A.5.16", "A.8.15", "A.8.16"],
            },
            {
                "key": "loki-syslog-unattended-upgrade",
                "days_ago": 2,
                "at": (3, 22),
                "system": "db01.demo.internal",
                "actor": "unattended-upgrades",
                "action": "package_update",
                "outcome": "success",
                "severity": 2,
                "summary": "Loki syslog db01 unattended security upgrade completed",
                "labels": {
                    "job": "varlogs",
                    "host": "db01.demo.internal",
                    "agent": "alloy",
                    "program": "unattended-upgrades",
                },
                "log": "unattended-upgrades: Packages that were upgraded: libssl3 openssl",
                "syslog": {
                    "facility": "daemon",
                    "program": "unattended-upgrades",
                    "packages": ["libssl3", "openssl"],
                },
                "controls": ["A.8.8", "A.8.15", "A.8.32"],
            },
            {
                "key": "loki-syslog-backup-complete",
                "days_ago": 1,
                "at": (1, 7),
                "system": "backup01.demo.internal",
                "actor": "systemd",
                "action": "backup_job",
                "outcome": "success",
                "severity": 2,
                "summary": "Loki syslog backup01 nightly object-storage backup completed",
                "labels": {
                    "job": "varlogs",
                    "host": "backup01.demo.internal",
                    "agent": "alloy",
                    "program": "systemd",
                },
                "log": "systemd[1]: media-backup.service: Succeeded after uploading 14 objects.",
                "syslog": {
                    "facility": "daemon",
                    "program": "systemd",
                    "unit": "media-backup.service",
                    "objects_uploaded": 14,
                },
                "controls": ["A.5.30", "A.8.13", "A.8.16"],
            },
        ]
        for spec in loki_events:
            timestamp = self._demo_event_timestamp(
                spec["days_ago"], spec["at"][0], spec["at"][1]
            )
            labels = spec["labels"]
            payload = {
                "labels": labels,
                "line": spec["log"],
                "parsed": {
                    "message": spec["log"],
                    "system": spec["system"],
                    "actor": spec["actor"],
                    "action": spec["action"],
                    "outcome": spec["outcome"],
                    "severity": spec["severity"],
                    "ossec": spec.get("ossec"),
                    "syslog": spec.get("syslog"),
                },
                "ingested_via": "Alloy remote syslog pipeline",
                "seed": {"prefix": self.prefix, "kind": "loki"},
            }
            events[spec["key"]] = self._seed_source_event(
                key=spec["key"],
                source="loki",
                timestamp=timestamp,
                system=spec["system"],
                actor=spec["actor"],
                action=spec["action"],
                outcome=spec["outcome"],
                severity=spec["severity"],
                summary=spec["summary"],
                raw_pointer={
                    "loki": {
                        "query_name": "demo-ossec-and-syslog",
                        "logql": '{job="varlogs", agent="alloy"}',
                        "labels": labels,
                        "ts_ns": str(int(timestamp.timestamp() * 1_000_000_000)),
                    }
                },
                normalized_payload=payload,
                controls=spec["controls"],
            )

        jenkins_events = [
            {
                "key": "jenkins-keen-api-deploy-success",
                "days_ago": 2,
                "at": (13, 5),
                "job": "deploy/keen-api",
                "build": 184,
                "result": "SUCCESS",
                "kind": "deployment",
                "label": "production",
                "actor": "jenkins",
                "summary": "Jenkins production keen-api deployment build 184 succeeded",
                "controls": ["A.8.25", "A.8.28", "A.8.32"],
            },
            {
                "key": "jenkins-ossec-rules-test-failed",
                "days_ago": 4,
                "at": (16, 20),
                "job": "security/ossec-rules-test",
                "build": 57,
                "result": "FAILURE",
                "kind": "security-test",
                "label": "security",
                "actor": "jenkins",
                "summary": "Jenkins OSSEC rules regression test build 57 failed",
                "controls": ["A.5.24", "A.8.8", "A.8.29"],
            },
            {
                "key": "jenkins-restore-smoke-test-success",
                "days_ago": 1,
                "at": (6, 30),
                "job": "backup/restore-smoke-test",
                "build": 22,
                "result": "SUCCESS",
                "kind": "resilience-test",
                "label": "continuity",
                "actor": "jenkins",
                "summary": "Jenkins restore smoke test build 22 succeeded",
                "controls": ["A.5.30", "A.8.13", "A.8.14"],
            },
        ]
        for spec in jenkins_events:
            timestamp = self._demo_event_timestamp(
                spec["days_ago"], spec["at"][0], spec["at"][1]
            )
            result = spec["result"].lower()
            events[spec["key"]] = self._seed_source_event(
                key=spec["key"],
                source="jenkins",
                timestamp=timestamp,
                system=spec["label"],
                actor=spec["actor"],
                action=spec["kind"],
                outcome="failed" if result == "failure" else "success",
                severity=4 if result == "failure" else 3,
                summary=spec["summary"],
                raw_pointer={
                    "jenkins": {
                        "job": spec["job"],
                        "build_number": spec["build"],
                        "url": f"https://jenkins.example.invalid/job/{spec['job']}/{spec['build']}/",
                    }
                },
                normalized_payload={
                    "job": spec["job"],
                    "build": spec["build"],
                    "label": spec["label"],
                    "kind": spec["kind"],
                    "result": spec["result"],
                    "seed": {"prefix": self.prefix, "kind": "jenkins"},
                },
                controls=spec["controls"],
            )

        github_events = [
            {
                "key": "github-push-security-headers",
                "days_ago": 6,
                "at": (10, 12),
                "type": "PushEvent",
                "actor": "dependabot[bot]",
                "repo": "demo-org/keen-demo",
                "summary": "GitHub push updates security headers dependency lockfile",
                "payload": {
                    "size": 2,
                    "ref": "refs/heads/main",
                    "commits": [
                        {"sha": "b6f3c12", "message": "Update CSP test fixtures"},
                        {
                            "sha": "9ac01bb",
                            "message": "Bump security middleware dependency",
                        },
                    ],
                },
                "controls": ["A.8.25", "A.8.28", "A.8.29"],
            },
            {
                "key": "github-pr-logging-merged",
                "days_ago": 5,
                "at": (15, 44),
                "type": "PullRequestEvent",
                "actor": "alice-security",
                "repo": "demo-org/keen-demo",
                "summary": "GitHub pull request merged for audit logging improvements",
                "payload": {
                    "action": "closed",
                    "pull_request": {
                        "number": 42,
                        "merged": True,
                        "title": "Improve audit logging for ISMS entity updates",
                    },
                },
                "controls": ["A.8.15", "A.8.16", "A.8.28"],
            },
            {
                "key": "github-release-v120",
                "days_ago": 1,
                "at": (17, 2),
                "type": "ReleaseEvent",
                "actor": "release-bot",
                "repo": "demo-org/keen-demo",
                "summary": "GitHub release published for KEEN demo version 1.2.0",
                "payload": {
                    "action": "published",
                    "release": {
                        "tag_name": "v1.2.0-demo",
                        "name": "KEEN Demo v1.2.0",
                        "draft": False,
                        "prerelease": False,
                    },
                },
                "controls": ["A.8.25", "A.8.31", "A.8.32"],
            },
        ]
        for spec in github_events:
            timestamp = self._demo_event_timestamp(
                spec["days_ago"], spec["at"][0], spec["at"][1]
            )
            github_event = {
                "id": f"demo-{spec['key']}",
                "type": spec["type"],
                "actor": {"login": spec["actor"]},
                "repo": {"name": spec["repo"]},
                "created_at": timestamp.isoformat() + "Z",
                "payload": spec["payload"],
            }
            events[spec["key"]] = self._seed_source_event(
                key=spec["key"],
                source="github",
                timestamp=timestamp,
                system=spec["repo"],
                actor=spec["actor"],
                action=spec["type"],
                outcome="success",
                severity=3,
                summary=spec["summary"],
                raw_pointer={
                    "github": {
                        "endpoint": "events",
                        "owner": spec["repo"].split("/", 1)[0],
                        "repo": spec["repo"].split("/", 1)[1],
                        "id": github_event["id"],
                        "type": spec["type"],
                        "created_at": github_event["created_at"],
                    }
                },
                normalized_payload={
                    "github_event": github_event,
                    "seed": {"prefix": self.prefix, "kind": "github"},
                },
                controls=spec["controls"],
            )

        forgejo_events = [
            {
                "key": "forgejo-someapp-push",
                "days_ago": 7,
                "at": (18, 5),
                "repo": "mig5/someapp",
                "actor": "mig5",
                "op": "push",
                "summary": "Forgejo mig5/someapp push updates grouped role generation",
                "activity": {
                    "content": "Push to main: some commit message",
                    "ref_name": "main",
                    "commit_id": "8774d019d3",
                },
                "controls": ["A.8.25", "A.8.28", "A.8.32"],
            },
            {
                "key": "forgejo-keen-pr-review",
                "days_ago": 3,
                "at": (11, 24),
                "repo": "mig5/keen-demo",
                "actor": "platform-reviewer",
                "op": "pull_request_review",
                "summary": "Forgejo KEEN demo pull request review approved audit sampling fix",
                "activity": {
                    "content": "Approved scoped evidence sampling fix for non-event entities",
                    "pull_request": {
                        "number": 73,
                        "title": "Fix audit sampling for ISMS entities",
                    },
                },
                "controls": ["A.8.25", "A.8.28", "A.8.29"],
            },
            {
                "key": "forgejo-keen-release",
                "days_ago": 1,
                "at": (19, 50),
                "repo": "mig5/keen-demo",
                "actor": "release-bot",
                "op": "release",
                "summary": "Forgejo KEEN demo release published with ISMS seed data",
                "activity": {
                    "content": "Release v1.2.0-demo published",
                    "release": {
                        "tag_name": "v1.2.0-demo",
                        "title": "KEEN demo seed data",
                    },
                },
                "controls": ["A.8.25", "A.8.31", "A.8.32"],
            },
        ]
        for spec in forgejo_events:
            timestamp = self._demo_event_timestamp(
                spec["days_ago"], spec["at"][0], spec["at"][1]
            )
            host = "git.mig5.net"
            owner, repo = spec["repo"].split("/", 1)
            activity = {
                "id": f"demo-{spec['key']}",
                "op_type": spec["op"],
                "act_user": {"login": spec["actor"]},
                "repo": {"owner_name": owner, "name": repo},
                "created": timestamp.isoformat() + "Z",
                **spec["activity"],
            }
            events[spec["key"]] = self._seed_source_event(
                key=spec["key"],
                source="forgejo",
                timestamp=timestamp,
                system=f"{host}/{spec['repo']}",
                actor=spec["actor"],
                action=spec["op"],
                outcome="success",
                severity=3,
                summary=spec["summary"],
                raw_pointer={
                    "forgejo": {
                        "host": host,
                        "owner": owner,
                        "repo": repo,
                        "activity_id": activity["id"],
                        "url": f"https://{host}/{spec['repo']}",
                    }
                },
                normalized_payload={
                    "activity": activity,
                    "feed_url": f"https://{host}/{spec['repo']}.rss",
                    "seed": {"prefix": self.prefix, "kind": "forgejo"},
                },
                controls=spec["controls"],
            )

        webhook_events = [
            {
                "key": "webhook-something-app01",
                "days_ago": 4,
                "at": (8, 40),
                "event_type": "event",
                "summary": "Webhook captured app01 host state",
                "payload": {
                    "generated_at": None,
                    "host": "app01.demo.internal",
                    "command": "some-command --output /var/lib/overthere/app01",
                    "mode": "event",
                    "harvest": {
                        "some_key": 612,
                        "some_other_thing": ["hello", "world"],
                        "some_boolean": True,
                    },
                },
                "controls": ["A.5.9", "A.8.9", "A.8.16"],
            },
            {
                "key": "webhook-something-bastion",
                "days_ago": 1,
                "at": (9, 10),
                "event_type": "manifest",
                "summary": "Webhook captured an event on bastion",
                "payload": {
                    "generated_at": None,
                    "host": "bastion01.demo.internal",
                    "new": {"host": "bastion01.demo.internal"},
                    "command": "foobar --fqdn bastion01.demo.internal --debug",
                    "mode": "manifest",
                    "manifest": {
                        "output_mode": "multi-site",
                        "roles": [
                            "services",
                            "users",
                            "firewall_runtime",
                            "etc_custom",
                        ],
                        "inventory_path": "inventory/host_vars/bastion01.demo.internal",
                        "playbook": "playbook.yml",
                    },
                },
                "controls": ["A.5.9", "A.8.9", "A.8.25", "A.8.32"],
            },
        ]
        for spec in webhook_events:
            timestamp = self._demo_event_timestamp(
                spec["days_ago"], spec["at"][0], spec["at"][1]
            )
            payload = dict(spec["payload"])
            payload["generated_at"] = timestamp.isoformat() + "Z"
            payload["seed"] = {"prefix": self.prefix, "kind": "webhook:something"}
            events[spec["key"]] = self._seed_source_event(
                key=spec["key"],
                source="webhook:something",
                timestamp=timestamp,
                system=payload.get("host") or payload.get("new", {}).get("host"),
                actor="something",
                action=spec["event_type"],
                outcome="info",
                severity=3,
                summary=spec["summary"],
                raw_pointer={
                    "webhook": {
                        "provider": "something",
                        "event_type": spec["event_type"],
                        "headers": {"X-Enroll-Secret": "<redacted>"},
                    }
                },
                normalized_payload=payload,
                controls=spec["controls"],
            )

        return events

    def seed_audits(
        self, docs: dict[str, IsmsDocument], diary_events: dict[str, Event]
    ) -> None:
        specs = [
            {
                "key": "access_audit",
                "title": "Access Control and Operations Internal Audit",
                "status": "in_progress",
                "audit_type": "internal",
                "start_offset": -5,
                "end_offset": 10,
                "controls": ["A.5.15", "A.5.16", "A.5.18", "A.8.2", "A.8.15"],
                "clauses": ["6.1", "7.2", "7.3", "8.1", "9.1"],
                "documents": ["security_policy", "access_procedure"],
                "diary": ["access_review", "management_review"],
                "summary": "In-progress sample audit focused on access control, privileged access, and operational logging evidence.",
            },
            {
                "key": "supplier_audit",
                "title": "Supplier and Interested Parties Assurance Audit",
                "status": "open",
                "audit_type": "external",
                "start_offset": 15,
                "end_offset": 30,
                "controls": ["A.5.19", "A.5.20", "A.5.21", "A.5.22", "A.5.23"],
                "clauses": ["4.2", "6.1", "8.1", "9.1"],
                "documents": ["supplier_standard", "incident_process"],
                "diary": ["supplier_assurance"],
                "summary": "Open sample audit for supplier governance, hosting assurance, and interested-party communication expectations.",
            },
            {
                "key": "incident_audit",
                "title": "Incident Preparedness and Continuity Audit",
                "status": "completed",
                "audit_type": "internal",
                "start_offset": -45,
                "end_offset": -30,
                "controls": [
                    "A.5.24",
                    "A.5.25",
                    "A.5.26",
                    "A.5.27",
                    "A.5.30",
                    "A.8.13",
                ],
                "clauses": ["6.1", "7.4", "8.1", "9.2", "10.1"],
                "documents": ["incident_process", "secure_dev_guideline"],
                "diary": ["tabletop", "management_review"],
                "summary": "Completed sample audit showing read-only completed audit behaviour with scoped incident and continuity evidence.",
            },
        ]

        for spec in specs:
            audit = self.upsert(
                Audit,
                {"title": self.label(spec["title"])},
                {
                    "framework_slug": self.framework,
                    "status": spec["status"],
                    "audit_type": spec["audit_type"],
                    "start_date": date.today()
                    + timedelta(days=int(spec["start_offset"])),
                    "end_date": date.today() + timedelta(days=int(spec["end_offset"])),
                    "notes": "Seeded demonstration audit. Scope and sampled evidence can be adjusted from the UI.",
                    "executive_summary": spec["summary"],
                    "meta": {"seed": "demo", "prefix": self.prefix, "key": spec["key"]},
                    "created_by_user_id": self.user.id,
                },
            )
            self.ensure_link(
                AuditAttendee,
                {
                    "audit_id": audit.id,
                    "user_id": self.user.id,
                    "name": self.user.username or self.user.email or "Demo user",
                    "role": "Lead auditor",
                },
                {"email": self.user.email},
            )
            self.ensure_link(
                AuditAttendee,
                {
                    "audit_id": audit.id,
                    "user_id": None,
                    "name": self.label("External Demonstration Auditor"),
                    "role": "Observer",
                },
                {"email": "demo-auditor@example.invalid"},
            )
            self._scope_audit_controls(audit, spec["controls"])
            self._scope_audit_clauses(audit, spec["clauses"])
            audit_docs = [docs[key] for key in spec["documents"] if key in docs]
            self._scope_audit_documents(audit, audit_docs)
            for doc in audit_docs:
                self._sample_entity_into_audit(
                    audit,
                    "isms_document",
                    doc.id,
                    f"Sampled document: {doc.title}",
                    f"{doc.document_type.title()} included as sample evidence for {audit.title}.",
                    "/isms.html?tab=documents",
                )
            for key in spec["diary"]:
                event = diary_events.get(key)
                if not event:
                    continue
                self._sample_event_into_audit(
                    audit,
                    event,
                    event.summary.replace("diary: ", "", 1),
                    f"Diary entry sampled into {audit.title}.",
                )

    def month_period(self, months_back: int) -> tuple[date, date]:
        today = date.today()
        month = today.month - months_back
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        next_month = month + 1
        next_year = year
        if next_month == 13:
            next_month = 1
            next_year += 1
        end = date(next_year, next_month, 1) - timedelta(days=1)
        return start, end

    def seed_effectiveness_measures(self) -> dict[str, IsmsEffectivenessMeasure]:
        specs = {
            "access_reviews": {
                "summary": "Access reviews completed on time",
                "description": "Percentage of scheduled access reviews completed by the due date.",
                "measure": "Review completion is tracked monthly by the ISMS owner.",
                "metric": "Access reviews completed on time (%)",
                "target": 95.0,
                "unit": "%",
                "frequency": "Monthly",
                "values": [90.0, 96.0, 100.0],
                "controls": ["A.5.16", "A.5.18", "A.8.2"],
            },
            "patch_sla": {
                "summary": "Critical patch remediation within SLA",
                "description": "Percentage of critical patches remediated inside the approved SLA.",
                "measure": "Patch reports are reviewed monthly and exceptions are tracked.",
                "metric": "Critical patches remediated within SLA (%)",
                "target": 90.0,
                "unit": "%",
                "frequency": "Monthly",
                "values": [88.0, 92.0, 94.0],
                "controls": ["A.8.8", "A.8.19", "A.8.32"],
            },
            "tabletop_actions": {
                "summary": "Incident tabletop actions closed",
                "description": "Percentage of tabletop exercise actions closed by agreed date.",
                "measure": "Tabletop actions are tracked after each exercise.",
                "metric": "Exercise actions closed (%)",
                "target": 100.0,
                "unit": "%",
                "frequency": "Quarterly",
                "values": [75.0, 100.0, 100.0],
                "controls": ["A.5.24", "A.5.27", "A.5.30"],
            },
            "restore_validation": {
                "summary": "Backup restore validation result",
                "description": "Qualitative pass/fail status from scheduled restore testing.",
                "measure": "Restore tests are performed and reviewed by the platform team.",
                "metric": "Restore test result",
                "target": None,
                "unit": "result",
                "frequency": "Monthly",
                "values": ["Pass", "Pass", "Pass"],
                "controls": ["A.5.30", "A.8.13", "A.8.14"],
            },
        }
        measures: dict[str, IsmsEffectivenessMeasure] = {}
        for key, spec in specs.items():
            metric_key = f"{self.account_code('METRIC')}-{key}"[:128]
            measure = self.upsert(
                IsmsEffectivenessMeasure,
                {"metric_key": metric_key},
                {
                    "framework_slug": self.framework,
                    "summary": self.label(spec["summary"]),
                    "description": spec["description"],
                    "effectiveness_measure": spec["measure"],
                    "metric": spec["metric"],
                    "target_value": spec["target"],
                    "target_unit": spec["unit"],
                    "threshold_operator": "gte" if spec["target"] is not None else "",
                    "owner_user_id": self.user.id,
                    "frequency": spec["frequency"],
                    "notes": "Seeded demonstration effectiveness measure.",
                    "created_by_user_id": self.user.id,
                },
            )
            self.link_isms_controls(
                "effectiveness_measure", measure.id, spec["controls"]
            )
            for index, value in enumerate(spec["values"], start=3):
                start, end = self.month_period(index - 1)
                metric_values: dict[str, Any]
                if isinstance(value, str):
                    metric_values = {
                        "metric_value": None,
                        "metric_unit": spec["unit"],
                        "qualitative_value": value,
                    }
                else:
                    metric_values = {
                        "metric_value": float(value),
                        "metric_unit": spec["unit"],
                        "qualitative_value": "",
                    }
                self.upsert(
                    IsmsEffectivenessMetricEntry,
                    {
                        "measure_id": measure.id,
                        "period_start": start,
                        "source_reference": f"{self.prefix}-{key}-{start:%Y-%m}",
                    },
                    {
                        "period_end": end,
                        "recorded_at": datetime.combine(end, time(12, 0)),
                        "source_type": "manual",
                        "source_title": self.label(f"{spec['summary']} sample metric"),
                        "source_url": None,
                        "notes": "Seeded sample metric for demonstration dashboards.",
                        "raw_payload": {"seed": "demo", "prefix": self.prefix},
                        "created_by_user_id": self.user.id,
                        **metric_values,
                    },
                )
            measures[key] = measure
        return measures

    def seed_objectives(self) -> dict[str, IsmsObjective]:
        target_1 = (date.today() + timedelta(days=90)).isoformat()
        target_2 = (date.today() + timedelta(days=180)).isoformat()
        specs = {
            "evidence_coverage": {
                "requirement": "Improve ISMS visibility and evidence coverage across controls.",
                "goal": "Increase mapped evidence coverage for priority controls.",
                "metric": "% of priority controls with current evidence in the last 90 days.",
                "method": "Review unmapped evidence, refine mapping rules, and sample evidence into audits.",
                "resources": "ISMS Owner, Security Lead, Platform Engineer.",
                "target": target_1,
                "evaluation": "Monthly management review of evidence coverage and gaps.",
                "status": "in_progress",
                "controls": ["A.5.1", "A.5.35", "A.5.36"],
            },
            "supplier_assurance": {
                "requirement": "Maintain assurance over managed hosting and support suppliers.",
                "goal": "Complete supplier assurance review for managed hosting providers.",
                "metric": "% of critical suppliers reviewed this cycle.",
                "method": "Request assurance evidence, review contractual security terms, and track actions.",
                "resources": "ISMS Owner and Supplier Management process owner.",
                "target": target_2,
                "evaluation": "Supplier review meeting and action closure rate.",
                "status": "in_progress",
                "controls": ["A.5.19", "A.5.20", "A.5.21", "A.5.22"],
            },
            "access_reviews": {
                "requirement": "Reduce privileged access risk.",
                "goal": "Reduce overdue privileged access reviews to zero.",
                "metric": "Number of overdue privileged access reviews.",
                "method": "Schedule monthly review and escalate overdue approvals.",
                "resources": "Security Lead and Platform Engineer.",
                "target": target_1,
                "evaluation": "Access review effectiveness measure trend.",
                "status": "in_progress",
                "controls": ["A.5.15", "A.5.16", "A.5.18", "A.8.2"],
            },
        }
        objectives: dict[str, IsmsObjective] = {}
        for key, spec in specs.items():
            obj = self.upsert(
                IsmsObjective,
                {"goal": self.label(spec["goal"])},
                {
                    "requirement": spec["requirement"],
                    "metric": spec["metric"],
                    "completion_method": spec["method"],
                    "resource_requirements_text": spec["resources"],
                    "owner_user_id": self.user.id,
                    "completion_target_date": spec["target"],
                    "evaluation_method": spec["evaluation"],
                    "status": spec["status"],
                    "created_by_user_id": self.user.id,
                },
            )
            self.ensure_link(
                IsmsObjectiveResourceUser,
                {"objective_id": obj.id, "user_id": self.user.id},
            )
            self.link_isms_controls("objective", obj.id, spec["controls"])
            objectives[key] = obj
        return objectives

    def seed_meetings(self, docs: dict[str, IsmsDocument]) -> None:
        specs = [
            {
                "title": "Monthly ISMS Review",
                "days_ago": 14,
                "start": time(10, 0),
                "end": time(11, 0),
                "notes": "Agenda:\n- Review open ISMS objectives\n- Review access review effectiveness metrics\n- Confirm supplier assurance actions\n\nMinutes:\nAccess reviews improved after reminders. Supplier assurance follow-up remains open.",
                "doc": "security_policy",
            },
            {
                "title": "Incident Tabletop Retrospective",
                "days_ago": 45,
                "start": time(14, 0),
                "end": time(15, 0),
                "notes": "Agenda:\n- Walk through customer-impacting outage scenario\n- Check incident communication steps\n- Capture lessons learned\n\nMinutes:\nCommunication templates need updating. Restore validation was completed successfully.",
                "doc": "incident_process",
            },
            {
                "title": "Managed Hosting Supplier Review",
                "days_ago": 30,
                "start": time(9, 30),
                "end": time(10, 15),
                "notes": "Agenda:\n- Review supplier assurance evidence\n- Check outstanding access and logging actions\n- Confirm next quarterly review date\n\nMinutes:\nSupplier evidence received. Logging retention action assigned to Platform Engineer.",
                "doc": "supplier_standard",
            },
        ]
        for spec in specs:
            meeting = self.upsert(
                IsmsMeeting,
                {
                    "title": self.label(spec["title"]),
                    "date": date.today() - timedelta(days=spec["days_ago"]),
                },
                {
                    "start_time": spec["start"],
                    "end_time": spec["end"],
                    "agenda_minutes_notes": spec["notes"],
                    "created_by_user_id": self.user.id,
                },
            )
            self.ensure_link(
                IsmsMeetingAttendee,
                {
                    "meeting_id": meeting.id,
                    "user_id": self.user.id,
                    "attendance_type": "attendee",
                },
            )
            doc = docs.get(spec["doc"])
            if doc:
                self.ensure_link(
                    IsmsMeetingLink,
                    {"meeting_id": meeting.id, "document_id": doc.id},
                    {"link_type": "document", "title": doc.title, "url": None},
                )
            self.link_isms_controls(
                "meeting",
                meeting.id,
                ["A.5.1", "A.5.35", "A.5.36"],
            )

    def run(self) -> None:
        print(
            f"Seeding KEEN demo data as user {self.user.username!r} "
            f"for framework {self.framework!r} with prefix {self.prefix!r}."
        )
        self.ensure_relevance_levels()
        org_nodes = self.seed_org_nodes()
        licenses = self.seed_licenses()
        assets = self.seed_assets(licenses, org_nodes)
        accounts = self.seed_hosting_accounts()
        processes = self.seed_business_processes()
        self.seed_risks(assets)
        self.seed_pestle(processes)
        self.seed_interested_parties()
        self.seed_access_matrix(assets, accounts, org_nodes)
        docs = self.seed_documents()
        diary_events = self.seed_diary_events()
        self.seed_source_events()
        self.seed_audits(docs, diary_events)
        self.seed_effectiveness_measures()
        self.seed_objectives()
        self.seed_meetings(docs)

    def print_summary(self) -> None:
        print("\nSummary:")
        for key in sorted(self.stats):
            print(f"  {key}: {self.stats[key]}")
        if self.warnings:
            print("\nWarnings:")
            for category, values in sorted(self.warnings.items()):
                joined = ", ".join(sorted(values))
                print(f"  {category}: {joined}")


def parse_args() -> DemoConfig:
    parser = argparse.ArgumentParser(
        description="Seed KEEN with deterministic demo ISMS/risk data."
    )
    parser.add_argument(
        "--framework",
        default=settings.default_framework_slug,
        help="Framework slug to use for control/clause mappings. Defaults to KEEN settings.default_framework_slug.",
    )
    parser.add_argument(
        "--prefix",
        default="Demo",
        help="Prefix added to seeded records. Reuse the same prefix for idempotent updates.",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Active KEEN username to own created records. Defaults to first active admin, then first active user.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all inserts/updates and then roll back instead of committing.",
    )
    args = parser.parse_args()
    return DemoConfig(
        framework=args.framework,
        prefix=args.prefix,
        username=args.username,
        dry_run=args.dry_run,
    )


def main() -> int:
    config = parse_args()
    db = SessionLocal()
    try:
        seeder = DemoSeeder(db, config)
        seeder.run()
        if config.dry_run:
            db.rollback()
            print("\nDry run complete; transaction rolled back.")
        else:
            db.commit()
            print("\nDemo data committed.")
        seeder.print_summary()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
