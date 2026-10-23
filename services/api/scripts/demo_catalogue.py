"""Fictional, offline examples. Explicit framework references; no inferred equivalence."""

import hashlib
from datetime import datetime, time, timedelta
from html import escape

from app.db.models import (
    Audit,
    AuditEvidence,
    AuditScopedControl,
    ControlItem,
    Event,
    IsmsDocument,
    IsmsDocumentFolder,
    IsmsDocumentRevision,
    IsmsEntityControlLink,
    IsmsPerson,
    IsmsPersonAssurance,
    IsmsVendor,
    Mapping,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
)

CATALOGUE = {
    "iso_27001_2022": [
        (
            "A.5.9",
            "Asset register reconciliation",
            "12 fictional devices reconciled; one retired laptop removed.",
        ),
        (
            "A.5.18",
            "Quarterly access review",
            "17 fictional accounts reviewed; two revoked and one exception assigned an expiry.",
        ),
        (
            "A.8.8",
            "Vulnerability remediation",
            "A simulated critical update was applied to three lab servers.",
        ),
        (
            "A.8.13",
            "Restore exercise",
            "A synthetic dataset was restored and verified in an isolated test environment.",
        ),
        (
            "A.5.24",
            "Incident tabletop",
            "Fictional responders exercised escalation and evidence preservation.",
        ),
        (
            "A.6.3",
            "Awareness completion",
            "Five of six fictional staff completed training; one follow-up remains open.",
        ),
    ],
    "KEEN-AF:1.0": [
        (
            "IAC-7",
            "Access review exception",
            "A temporary support role requires a documented review before renewal.",
        ),
        (
            "BCD-2",
            "Recovery rehearsal",
            "Sample recovery plan exercised; measured restore time 42 minutes.",
        ),
        (
            "TPM-2",
            "Supplier assurance review",
            "Fictional hosting supplier reviewed; a contract action remains open.",
        ),
        (
            "SAT-1",
            "Staff training review",
            "Fictional training register reviewed by the assurance lead.",
        ),
        (
            "VPM-2",
            "Lab vulnerability review",
            "Three synthetic findings triaged; one remediation is overdue.",
        ),
        (
            "GOV-11",
            "Policy review",
            "The fictional board approved a revised information-security policy.",
        ),
    ],
    "CYBER-ESSENTIALS:2026": [
        (
            "A4.6",
            "Firewall rule review",
            "Annual review of the fictional boundary removed an unused inbound rule.",
        ),
        (
            "A5.1",
            "Unused software removal",
            "An unneeded lab service was uninstalled and its listener verified closed.",
        ),
        (
            "A6.4",
            "Security update timing",
            "Synthetic critical OS updates installed within 10 days; no real host was scanned.",
        ),
        (
            "A7.3",
            "Leaver account closure",
            "A fictional leaver account was disabled on the agreed departure date.",
        ),
        (
            "A7.16",
            "Administrator MFA check",
            "All three fictional cloud administrators use MFA; test evidence only.",
        ),
        (
            "A8.3",
            "Malware scan review",
            "A synthetic scan log records a successful scheduled scan of lab endpoints.",
        ),
    ],
    "soc2_tsc": [
        (
            "CC6.3",
            "Quarterly user access review",
            "23 fictional production accounts reconciled against HR records; three removed and one role reduced.",
        ),
        (
            "CC8.1",
            "Change approval sampling",
            "25 fictional production changes sampled; each had peer review and ticket approval, and one emergency change was approved retrospectively.",
        ),
        (
            "CC7.2",
            "Security alert monitoring review",
            "A week of synthetic monitoring alerts triaged within the agreed response time; no incident declared.",
        ),
        (
            "A1.2",
            "Backup job verification",
            "A month of synthetic nightly backups verified; one failed job ran again successfully the same morning.",
        ),
        (
            "CC9.2",
            "Vendor risk assessment",
            "A fictional critical vendor's SOC 2 Type 2 report reviewed and its complementary user entity controls assigned owners.",
        ),
        (
            "CC1.4",
            "Security training completion",
            "Annual awareness training completed by 18 of 19 fictional staff; one reminder sent.",
        ),
    ],
    "pci_dss_v4": [
        (
            "11.3",
            "Quarterly vulnerability scans",
            "Synthetic internal and external scans of the fictional cardholder data environment show no high-risk findings outstanding.",
        ),
        (
            "8.4",
            "MFA into the cardholder data environment",
            "Every fictional administrative path into the CDE requires MFA; a test sign-in without the second factor was refused.",
        ),
        (
            "10.4",
            "Daily security log review",
            "Synthetic CDE security events reviewed daily; two anomalies investigated and closed.",
        ),
        (
            "6.3.3",
            "Critical patch installation",
            "Synthetic critical patches installed on fictional CDE hosts within one month of release.",
        ),
        (
            "12.6",
            "Security awareness for payment staff",
            "Fictional payment operations staff completed awareness training covering phishing and social engineering.",
        ),
        (
            "3.5",
            "Stored PAN discovery scan",
            "A synthetic data discovery scan found no unprotected PAN outside the tokenisation vault.",
        ),
    ],
}
# The catalogue's fictional suppliers; other demo records refer to them by name.
VENDORS = (
    "Example Hosting",
    "Example Identity Services",
    "Example Device Supply",
    "Example Payments",
)


def ensure_vendor(seeder, name):
    return seeder.upsert(
        IsmsVendor,
        {"name": seeder.label(name)},
        dict(
            description="Fictional supplier for demonstration only.",
            website="https://example.invalid",
            contact="support@example.invalid",
        ),
    )


def select_frameworks(seeder, frameworks, default=None):
    """Offer the seeded frameworks in selectors, keeping existing choices.

    ``default`` becomes the organisation default when it is offered. A
    framework that KEEN_ENABLED_FRAMEWORKS excludes stays excluded.
    """
    from copy import deepcopy

    from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
    from app.services.frameworks import framework_selection

    db = seeder.db
    # Lock the selection, as an administrator's save does, so a save made
    # while the seed runs is never overwritten.
    row = (
        db.query(ManagedConfiguration)
        .filter_by(name="organisation-frameworks")
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    current = framework_selection(db=db)
    allowed = {item["slug"] for item in current["items"] if item["allowed"]}
    enabled = {item["slug"] for item in current["items"] if item["enabled"]}
    enabled |= allowed & set(frameworks)
    document = {
        "enabled": sorted(enabled),
        "default": default if default in enabled else current["default"],
    }
    if row is not None and row.document == document:
        seeder.stats["organisation-frameworks.unchanged"] += 1
        return
    if row is None:
        row = ManagedConfiguration(
            name="organisation-frameworks",
            document=document,
            version=1,
            updated_by=seeder.user.id,
        )
        db.add(row)
    else:
        row.document = document
        row.version += 1
        row.updated_by = seeder.user.id
    db.add(
        ManagedConfigurationRevision(
            name="organisation-frameworks",
            version=row.version,
            document=deepcopy(document),
            updated_by=seeder.user.id,
        )
    )
    db.flush()
    seeder.stats["organisation-frameworks.updated"] += 1


def seed_catalogue(seeder, frameworks):
    from demo_metrics import seed_metrics

    seed_metrics(seeder, frameworks)
    db = seeder.db
    folder = seeder.upsert(
        IsmsDocumentFolder, {"name": seeder.label("Sample policies"), "parent_id": None}
    )
    for name, role in [
        ("Alex Example", "Assurance lead"),
        ("Sam Example", "Platform engineer"),
        ("Riley Example", "Service owner"),
        ("Jordan Example", "Support analyst"),
        ("Casey Example", "Privacy lead"),
        ("Morgan Example", "Contractor"),
    ]:
        person = seeder.upsert(
            IsmsPerson,
            {"name": seeder.label(name)},
            dict(
                email=name.split()[0].lower() + "@example.invalid",
                position=role,
                notes="Fictional demonstration person. Not a login account.",
            ),
        )
        for category, title in [
            ("Training", "Security awareness"),
            ("Policy acknowledgement", "Acceptable use acknowledgement"),
        ]:
            seeder.upsert(
                IsmsPersonAssurance,
                {"person_id": person.id, "name": title},
                dict(
                    category=category,
                    status="pending" if name.startswith("Morgan") else "met",
                    source_system="demo",
                    notes="Synthetic assurance record.",
                    completed_at=None
                    if name.startswith("Morgan")
                    else seeder.seed_date - timedelta(days=14),
                ),
            )
    for name in VENDORS:
        ensure_vendor(seeder, name)
    # The ISMS programme asset carries the example control gaps when the full
    # dataset exists; the order keeps repeated runs on the same asset.
    asset = (
        db.query(RiskAsset)
        .filter(RiskAsset.name.like(seeder.prefix + " %"))
        .order_by(
            (RiskAsset.name == seeder.label("ISMS Programme")).desc(), RiskAsset.name
        )
        .first()
    )
    if asset is None:
        category = seeder.upsert(
            RiskCategory, {"name": seeder.label("Fictional services")}
        )
        subcategory = seeder.upsert(
            RiskAssetSubcategory,
            {"category_id": category.id, "name": seeder.label("Lab systems")},
        )
        asset = seeder.upsert(
            RiskAsset,
            {"name": seeder.label("Fictional service")},
            {
                "description": "Synthetic service asset",
                "category_id": category.id,
                "subcategory_id": subcategory.id,
            },
        )
    for framework in frameworks:
        if framework not in CATALOGUE:
            raise ValueError("No reviewed demo catalogue for " + framework)
        examples = CATALOGUE[framework]
        for index, (ref, title, detail) in enumerate(examples):
            control = (
                db.query(ControlItem)
                .filter_by(framework_slug=framework, ref=ref)
                .one_or_none()
            )
            if control is None:
                raise ValueError(
                    f"Missing seeded control {framework}/{ref}; run migrations first"
                )
            key = f"{seeder.account_code('CATALOGUE')}-{framework}-{ref}"
            event = seeder.upsert(
                Event,
                {"source": "demo", "external_id": key},
                dict(
                    timestamp=datetime.combine(
                        seeder.seed_date - timedelta(days=index + 1), time(10, 0)
                    ),
                    system="fictional.example.invalid",
                    actor="demo-reviewer",
                    action="sample_control_review",
                    outcome="warning" if index % 3 == 0 else "success",
                    severity=3 if index % 3 == 0 else 1,
                    summary=seeder.label(title),
                    raw_pointer={"seed": "demo", "prefix": seeder.prefix},
                    normalized_payload={
                        "demo": True,
                        "details": detail,
                        "framework": framework,
                        "control": ref,
                        "notice": "Fictional evidence. Not proof of compliance.",
                    },
                ),
            )
            seeder.ensure_link(
                Mapping,
                {"event_id": event.id, "control_item_id": control.id},
                dict(
                    confidence=1.0,
                    method="seeded-demo",
                    mapped_by=seeder.user.username,
                    rationale="Fictional evidence specifically illustrating this control.",
                ),
            )
            if index < 2:
                html = f"<h2>Fictional sample: {escape(title)}</h2><p><strong>DEMO ONLY - not a policy for real use.</strong></p><p>{escape(detail)}</p><h3>Responsibilities</h3><p>The fictional service owner reviews exceptions monthly.</p><h3>Review</h3><p>Record the reviewer, decision, scope and next review date. Adapt a real policy to the organisation before use.</p>"
                doc_title = seeder.label(framework + " " + title)
                approved = datetime.combine(
                    seeder.seed_date - timedelta(days=index + 1), time(9, 0)
                )
                values = dict(
                    document_type="policy",
                    description="Fictional worked example",
                    folder_id=folder.id,
                    tags=["demo"],
                    created_by_user_id=seeder.user.id,
                    created_at=approved,
                    updated_at=approved,
                )
                current = db.query(IsmsDocument).filter_by(title=doc_title).first()
                # Content arrives once; later edits made in Keen are kept.
                if current is None or not current.content_version:
                    values.update(content_html=html, content_version=1)
                doc = seeder.upsert(IsmsDocument, {"title": doc_title}, values)
                if doc.content_version == 1 and doc.content_html == html:
                    seeder.ensure_link(
                        IsmsDocumentRevision,
                        {"document_id": doc.id, "version": 1},
                        dict(
                            content_html=html,
                            sha256=hashlib.sha256(html.encode()).hexdigest(),
                            created_by_user_id=seeder.user.id,
                            created_at=approved,
                        ),
                    )
                seeder.ensure_link(
                    IsmsEntityControlLink,
                    {
                        "entity_type": "document",
                        "entity_id": doc.id,
                        "framework_slug": framework,
                        "control_item_id": control.id,
                    },
                    {"created_by_user_id": seeder.user.id},
                )
                risk = seeder.upsert(
                    Risk,
                    {
                        "asset_id": asset.id,
                        "threat_summary": seeder.label(
                            framework + " " + title + " gap"
                        ),
                    },
                    dict(
                        title=title + " gap",
                        risk_types=["Confidentiality", "Integrity", "Availability"],
                        risk_owner_user_id=seeder.user.id,
                        **seeder.risk_ratings(3 + index, 4, 2, 3),
                        treatment_strategy="mitigate",
                        treatment_status="open",
                        treatment_plan="Fictional example: review the sample evidence and assign the remaining action.",
                        treatment_due_at=seeder.seed_date + timedelta(days=14),
                        note="Fictional demonstration scenario",
                        created_by_user_id=seeder.user.id,
                    ),
                )
                seeder.ensure_link(
                    RiskControlLink,
                    {
                        "risk_id": risk.id,
                        "framework_slug": framework,
                        "control_item_id": control.id,
                    },
                )
                audit = seeder.upsert(
                    Audit,
                    {
                        "title": seeder.label(framework + " " + title + " review"),
                        "framework_slug": framework,
                    },
                    dict(
                        status="open",
                        audit_type="internal",
                        start_date=seeder.seed_date,
                        end_date=seeder.seed_date + timedelta(days=7),
                        created_by_user_id=seeder.user.id,
                    ),
                )
                seeder.ensure_link(
                    AuditScopedControl,
                    {"audit_id": audit.id, "control_item_id": control.id},
                )
                seeder.ensure_link(
                    AuditEvidence,
                    {"audit_id": audit.id, "event_id": event.id},
                    dict(
                        title=event.summary,
                        notes="Synthetic sample reviewed in this fictional audit.",
                        added_by_user_id=seeder.user.id,
                    ),
                )

    from demo_swot import seed_swot

    seed_swot(seeder, frameworks)
