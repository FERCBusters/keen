"""Fictional SWOT context shared across demo frameworks; no inferred mappings."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from app.db.models import (
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsObjective,
    IsmsPerson,
    IsmsSwot,
    IsmsSwotLink,
    IsmsVendor,
    Risk,
)
from app.services.isms.common import _record, _utcnow
from app.services.isms.swot import summary

# Each tuple explicitly reviews applicability in ISO, Keen Assurance, CE, SOC 2
# and PCI DSS; None records that no control in that framework applies.
FRAMEWORKS = (
    "iso_27001_2022",
    "KEEN-AF:1.0",
    "CYBER-ESSENTIALS:2026",
    "soc2_tsc",
    "pci_dss_v4",
)
SPECS = (
    (
        "team",
        "Experienced incident-response team",
        "strength",
        4,
        "completed",
        None,
        "Fictional team members have rehearsed incident roles and share practical knowledge.",
        "Maintain cross-training and review lessons after every exercise.",
        ("A.6.3", "SAT-1", None, "CC1.4", "12.6"),
    ),
    (
        "access",
        "Established privileged-access safeguards",
        "strength",
        4,
        "under_review",
        None,
        "Fictional administrators use strong authentication and documented access reviews.",
        "Validate exceptions and retain review evidence each quarter.",
        ("A.5.18", "IAC-7", "A7.16", "CC6.3", "8.4"),
    ),
    (
        "manual",
        "Manual evidence collection creates review delays",
        "weakness",
        3,
        "in_progress",
        None,
        "Demonstration reviews rely on spreadsheets and repeated requests for evidence.",
        "Automate collection for the priority control set and measure timely coverage.",
        ("A.5.9", "GOV-11", "A4.6", "CC4.1", None),
    ),
    (
        "supplier",
        "Incomplete supplier assurance follow-up",
        "weakness",
        4,
        "planned",
        None,
        "Fictional supplier reviews contain outstanding assurance and contract actions.",
        "Assign action owners and review supplier evidence before renewal.",
        ("A.5.19", "TPM-2", None, "CC9.2", "12.8"),
    ),
    (
        "technology",
        "New technologies",
        "opportunity",
        3,
        "defined",
        None,
        "New tools may improve evidence quality and reduce repetitive assurance work.",
        "Evaluate small, measurable pilots with security and privacy review.",
        ("A.8.8", "VPM-2", "A6.4", "CC7.1", "6.3"),
    ),
    (
        "ai",
        "AI-assisted evidence triage",
        "opportunity",
        2,
        "planned",
        "technology",
        "A fictional pilot could help reviewers organise synthetic evidence and identify gaps.",
        "Use synthetic data only; keep human approval and measure precision before adoption.",
        ("A.5.1", "GOV-11", None, "CC5.3", None),
    ),
    (
        "pandemic",
        "Pandemic disruption",
        "threat",
        5,
        "planned",
        None,
        "A fictional pandemic scenario tests people and supplier continuity assumptions.",
        "Exercise continuity arrangements and track the two dependent scenarios below.",
        ("A.5.30", "BCD-2", None, "CC9.1", None),
    ),
    (
        "absence",
        "Sickness and staff absenteeism",
        "threat",
        4,
        "in_progress",
        "pandemic",
        "Multiple responders may be unavailable at once during a prolonged incident.",
        "Cross-train deputies and rehearse the reduced-staff incident-response procedure.",
        ("A.6.3", "SAT-1", None, "CC1.4", None),
    ),
    (
        "supply",
        "Supply-chain delays",
        "threat",
        4,
        "deferred",
        "pandemic",
        "Fictional device replacement and hosting support may be delayed by wider disruption.",
        "Review alternative suppliers and document acceptable recovery lead times.",
        ("A.5.21", "TPM-2", None, "CC9.2", "12.8"),
    ),
)


def seed_swot(seeder, frameworks):
    """Called after demo catalogue entities exist, within the caller's transaction."""
    from demo_catalogue import CATALOGUE

    db = seeder.db

    def identity(key):
        return uuid5(NAMESPACE_URL, f"keen:demo:swot:{seeder.prefix}:{key}")

    objectives = {}
    for key, goal, metric, method, status, days in [
        (
            "evidence",
            "Collect current evidence for 95% of priority controls",
            "Percentage of selected controls with reviewed evidence under 90 days old.",
            "Pilot automated collection; review gaps weekly.",
            "in_progress",
            90,
        ),
        (
            "supplier",
            "Complete assurance reviews for every critical supplier",
            "Percentage of due critical-supplier reviews completed; target 100%.",
            "Review evidence and contract actions with each supplier owner before renewal.",
            "under_review",
            60,
        ),
        (
            "continuity",
            "Validate two deputies for each critical response role",
            "Critical roles with two trained deputies; target 100%, restore rehearsal within 60 minutes.",
            "Cross-train deputies and rehearse recovery with reduced staffing.",
            "planned",
            120,
        ),
    ]:
        objectives[key] = seeder.upsert(
            IsmsObjective,
            {"id": identity("objective-" + key)},
            {
                "goal": seeder.label(goal),
                "requirement": "Fictional measurable response linked to SWOT context.",
                "metric": metric,
                "completion_method": method,
                "evaluation_method": "Compare the relevant monthly effectiveness trend and review source evidence at management review.",
                "resource_requirements_text": "Fictional assurance lead, platform engineer and service owner; two review sessions per month.",
                "owner_user_id": seeder.user.id,
                "created_by_user_id": seeder.user.id,
                "start_date": seeder.seed_date,
                "completion_target_date": (
                    seeder.seed_date + timedelta(days=days)
                ).isoformat(),
                "status": status,
            },
        )
    person = db.query(IsmsPerson).filter_by(name=seeder.label("Alex Example")).one()
    vendor = db.query(IsmsVendor).filter_by(name=seeder.label("Example Hosting")).one()
    for index, (
        key,
        title,
        kind,
        rating,
        status,
        parent,
        context,
        response,
        refs,
    ) in enumerate(SPECS):
        row = db.get(IsmsSwot, identity(key))
        created = row is None
        if created:
            row = IsmsSwot(
                id=identity(key),
                title=seeder.label(title),
                kind=kind,
                significance=rating,
                status=status,
                description=context,
                response=response,
                owner_user_id=seeder.user.id,
                created_by_user_id=seeder.user.id,
                parent_id=identity(parent) if parent else None,
                start_date=None if kind == "strength" else seeder.seed_date,
                target_date=None
                if kind == "strength"
                else seeder.seed_date + timedelta(days=30 + index * 10),
            )
            db.add(row)
            db.flush()
            seeder.stats["isms_swot.created"] += 1
        before = summary(row) if not created else None
        added = False

        def ensure(model, fields):
            nonlocal added
            if db.query(model).filter_by(**fields).first() is None:
                seeder.ensure_link(model, fields)
                added = True

        def relate(kind, target_id):
            ensure(
                IsmsSwotLink,
                {"swot_id": row.id, "target_type": kind, "target_id": target_id},
            )

        if key in {"team", "access", "absence"}:
            relate("person", person.id)
        if key in {"supplier", "pandemic", "supply"}:
            relate("vendor", vendor.id)
        objective = (
            objectives["evidence"]
            if key in {"manual", "technology", "ai"}
            else objectives["supplier"]
            if key in {"supplier", "supply"}
            else objectives["continuity"]
            if key in {"pandemic", "absence"}
            else None
        )
        if objective:
            relate("objective", objective.id)
        for framework in frameworks:
            ref = refs[FRAMEWORKS.index(framework)]
            if ref:
                control = (
                    db.query(ControlItem)
                    .filter_by(framework_slug=framework, ref=ref)
                    .one()
                )
                ensure(
                    IsmsEntityControlLink,
                    {
                        "entity_type": "swot",
                        "entity_id": row.id,
                        "framework_slug": framework,
                        "control_item_id": control.id,
                    },
                )
                if objective:
                    seeder.ensure_link(
                        IsmsEntityControlLink,
                        {
                            "entity_type": "objective",
                            "entity_id": objective.id,
                            "framework_slug": framework,
                            "control_item_id": control.id,
                        },
                    )
            if framework == "iso_27001_2022":
                clause = (
                    db.query(FrameworkClause)
                    .filter_by(
                        framework_slug=framework,
                        ref="6.2" if key in {"manual", "technology", "ai"} else "6.1.1",
                    )
                    .one_or_none()
                )
                if clause is not None:
                    ensure(
                        IsmsEntityClauseLink,
                        {
                            "entity_type": "swot",
                            "entity_id": row.id,
                            "framework_slug": framework,
                            "clause_id": clause.id,
                        },
                    )
            if key == "manual":
                # Exact seeded identities; never pick an unrelated real record.
                _, example, _ = CATALOGUE[framework][0]
                title = seeder.label(framework + " " + example)
                document = db.query(IsmsDocument).filter_by(title=title).one()
                risk = db.query(Risk).filter_by(threat_summary=title + " gap").one()
                relate("document", document.id)
                relate("risk", risk.id)
                relate("asset", risk.asset_id)
        if created or added:
            if not created:
                row.version += 1
                row.updated_at = _utcnow()
            after = summary(row)
            after["source"] = "fictional_demo_seed"
            # Avoid copying restricted relationship identities into general history.
            _record(
                db,
                "swot",
                row,
                "created" if created else "updated",
                before,
                after,
                seeder.user,
            )
        else:
            seeder.stats["isms_swot.unchanged"] += 1
