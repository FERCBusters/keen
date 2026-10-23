"""Fictional risk register: CIA triad risks and general risks, with treatments.

Ratings follow Keen's 5x5 register; the score columns derive from them as the
application derives them. Control links name explicit references per
framework; a framework the run does not seed is left out.
"""

from datetime import timedelta

from app.db.models import (
    InterestedParty,
    InterestedPartyName,
    Risk,
    RiskInterestedParty,
)
from demo_catalogue import ensure_vendor

C, IN, AV = "Confidentiality", "Integrity", "Availability"

# key: title, asset, CIA types, threat summary, (likelihood, impact, residual
# likelihood, residual impact), treatment strategy, status, plan, days until
# the treatment is due (negative when already past), owner role (None: the
# seeding user), control references per framework, and who the risk affects.
RISKS = {
    "admin_access": dict(
        title="Unauthorised administrative access to hosting",
        asset="platform",
        types=[C, IN, AV],
        summary="Misconfigured hosting permissions allow unauthorised administrative access.",
        ratings=(4, 5, 2, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Enforce single sign-on with MFA for console access, remove standing administrator roles and review privileged access monthly.",
        due=30,
        owner="security_lead",
        controls={
            "iso_27001_2022": ["A.5.15", "A.5.16", "A.8.2", "A.8.5", "A.8.15"],
            "soc2_tsc": ["CC6.1", "CC6.2"],
            "pci_dss_v4": ["7.2", "8.4"],
        },
        affects=["Customers"],
    ),
    "data_exposure": dict(
        title="Customer data exposed through weak access or retention",
        asset="data_store",
        types=[C],
        summary="Customer or audit evidence data is exposed through weak access review or retention controls.",
        ratings=(3, 5, 2, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Quarterly access reviews for the data store, automated deletion at the end of each retention period and alerts on bulk exports.",
        due=45,
        owner="dpo",
        controls={
            "iso_27001_2022": ["A.5.34", "A.8.3", "A.8.12", "A.8.24"],
            "soc2_tsc": ["C1.1", "CC6.1"],
        },
        affects=["Customers", "Regulators"],
    ),
    "release_defect": dict(
        title="Faulty release disrupts evidence collection",
        asset="web_app",
        types=[IN, AV],
        summary="Release or dependency defect disrupts evidence ingestion or audit workflows.",
        ratings=(3, 4, 2, 2),
        strategy="mitigate",
        status="closed",
        plan="Automated tests, a staged rollout and one-step rollback for every production release.",
        due=-10,
        owner=None,
        controls={
            "iso_27001_2022": ["A.8.8", "A.8.9", "A.8.25", "A.8.28", "A.8.32"],
            "soc2_tsc": ["CC8.1"],
        },
        affects=["Customers"],
    ),
    "key_person": dict(
        title="Key operations staff unavailable",
        asset="service_desk",
        types=[AV],
        summary="Key operations staff are unavailable during incident response or audit preparation.",
        ratings=(3, 3, 2, 2),
        strategy="mitigate",
        status="in_progress",
        plan="Cross-train two deputies for each critical response role and keep the runbooks current.",
        due=60,
        owner="isms_owner",
        controls={
            "iso_27001_2022": ["A.5.3", "A.5.30", "A.6.3", "A.6.5"],
            "soc2_tsc": ["CC1.4"],
        },
        affects=["Staff"],
    ),
    "ransomware": dict(
        title="Ransomware on production hosts",
        asset="platform",
        types=[C, IN, AV],
        summary="Ransomware delivered through a compromised administrator account or an unpatched service encrypts production hosts and attached storage.",
        ratings=(3, 5, 2, 4),
        strategy="mitigate",
        status="in_progress",
        plan="Immutable off-site backups, malware protection on servers, restricted administrative paths and monthly restore drills.",
        due=60,
        owner="platform_engineer",
        controls={
            "iso_27001_2022": ["A.8.7", "A.8.8", "A.8.13", "A.8.16", "A.5.30"],
            "soc2_tsc": ["A1.2", "CC7.2"],
        },
        affects=["Customers"],
    ),
    "phishing": dict(
        title="Phishing leads to account takeover",
        asset="collaboration",
        types=[C, IN],
        summary="A convincing phishing email captures a staff member's credentials and session, giving an attacker access to email and shared drives.",
        ratings=(4, 4, 2, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Phishing-resistant MFA for administrators, quarterly phishing simulations and a report-phishing button that feeds the incident process.",
        due=30,
        owner="security_lead",
        controls={
            "iso_27001_2022": ["A.5.17", "A.6.3", "A.6.8", "A.8.5", "A.8.7"],
            "soc2_tsc": ["CC6.1", "CC1.4"],
            "pci_dss_v4": ["5.4", "12.6"],
        },
        affects=["Staff"],
    ),
    "public_bucket": dict(
        title="Customer data bucket exposed publicly",
        asset="data_store",
        types=[C],
        summary="A storage bucket holding customer exports is made public by a misconfigured policy or infrastructure change.",
        ratings=(2, 5, 1, 5),
        strategy="mitigate",
        status="closed",
        plan="Account-level public access blocking, a configuration rule that alerts on public buckets and a weekly review of storage policies.",
        due=-14,
        owner="dpo",
        controls={
            "iso_27001_2022": ["A.5.23", "A.8.3", "A.8.9", "A.8.12"],
            "soc2_tsc": ["CC6.1", "CC6.7"],
        },
        affects=["Customers", "Regulators"],
    ),
    "lost_laptop": dict(
        title="Lost or stolen laptop",
        asset="laptops",
        types=[C],
        summary="A staff laptop is lost or stolen while travelling, exposing locally stored information and saved sessions.",
        ratings=(3, 3, 2, 2),
        strategy="mitigate",
        status="closed",
        plan="Full-disk encryption, device management with remote wipe, short session lifetimes and no customer data stored locally.",
        due=-30,
        owner="service_desk",
        controls={
            "iso_27001_2022": ["A.6.7", "A.7.9", "A.8.1", "A.8.24"],
        },
        affects=["Staff"],
    ),
    "leaver_access": dict(
        title="Former staff keep system access",
        asset="identity",
        types=[C, IN],
        summary="Accounts and tokens of leavers or contractors stay active after departure and are later misused.",
        ratings=(3, 4, 1, 4),
        strategy="mitigate",
        status="in_progress",
        plan="An HR-triggered leaver workflow, same-day single sign-on suspension, removal from source control and cloud consoles, and a monthly reconciliation.",
        due=21,
        owner="security_lead",
        controls={
            "iso_27001_2022": ["A.5.16", "A.5.18", "A.6.5", "A.8.2"],
            "soc2_tsc": ["CC6.2", "CC6.3"],
            "pci_dss_v4": ["8.2"],
        },
        affects=["Customers", "Staff"],
    ),
    "unpatched_vulnerability": dict(
        title="Unpatched internet-facing vulnerability exploited",
        asset="web_app",
        types=[C, IN, AV],
        summary="A critical vulnerability in an internet-facing component is exploited before the fix is deployed.",
        ratings=(4, 4, 2, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Weekly scanning, a 14-day target for critical patches, threat-intelligence triage and web application firewall rules as a stopgap.",
        due=14,
        owner="platform_engineer",
        controls={
            "iso_27001_2022": ["A.5.7", "A.8.8", "A.8.20", "A.8.29"],
            "soc2_tsc": ["CC7.1"],
            "pci_dss_v4": ["6.3.3", "11.3"],
        },
        affects=["Customers"],
    ),
    "card_data": dict(
        title="Card data captured outside the tokenisation flow",
        asset="payments",
        types=[C],
        summary="A change to the checkout page or a support process causes full card numbers to be captured, logged or stored outside the payment provider's hosted fields.",
        ratings=(2, 5, 1, 5),
        strategy="mitigate",
        status="open",
        plan="Keep the hosted payment fields, monitor payment-page scripts, run quarterly card data discovery scans and validate the PCI DSS scope every year.",
        due=45,
        owner=None,
        controls={
            "iso_27001_2022": ["A.5.14", "A.5.34", "A.8.11", "A.8.12"],
            "soc2_tsc": ["C1.1"],
            "pci_dss_v4": ["3.2", "3.5", "4.2", "6.4", "11.6", "12.5"],
        },
        affects=["Customers", "Regulators"],
        vendors=["Example Payments"],
    ),
    "supply_chain": dict(
        title="Malicious dependency enters the build pipeline",
        asset="source_code",
        types=[IN],
        summary="A compromised open-source package or build plugin injects malicious code into a release.",
        ratings=(3, 4, 2, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Pinned and reviewed dependencies, dependency scanning in CI, signed releases and least-privilege build credentials.",
        due=60,
        owner="security_lead",
        controls={
            "iso_27001_2022": ["A.5.21", "A.8.4", "A.8.25", "A.8.28"],
            "soc2_tsc": ["CC8.1"],
            "pci_dss_v4": ["6.2"],
        },
        affects=["Customers"],
    ),
    "restore_failure": dict(
        title="Backups cannot be restored within the recovery objective",
        asset="backups",
        types=[IN, AV],
        summary="Corrupt or incomplete backups, or an untested procedure, prevent restoring service within 60 minutes.",
        ratings=(2, 5, 1, 4),
        strategy="mitigate",
        status="closed",
        plan="Monthly measured restore tests against the 60-minute objective, immutable backup copies and a current restore runbook.",
        due=-7,
        owner="platform_engineer",
        controls={
            "iso_27001_2022": ["A.5.30", "A.5.37", "A.8.13", "A.8.14"],
            "soc2_tsc": ["A1.2", "A1.3"],
        },
        affects=["Customers"],
    ),
    "office_outage": dict(
        title="Office power or environmental failure",
        asset="office",
        types=[AV],
        summary="Loss of power, cooling or access to the office interrupts on-site support and network equipment.",
        ratings=(2, 3, 1, 3),
        strategy="accept",
        status="closed",
        plan="Accepted by the board: a UPS covers the communications room, staff can work remotely and the generator contract is reviewed every year.",
        due=None,
        owner="isms_owner",
        controls={"iso_27001_2022": ["A.5.29", "A.7.5", "A.7.11"]},
        affects=["Staff"],
    ),
    "denial_of_service": dict(
        title="Denial of service on the customer portal",
        asset="web_app",
        types=[AV],
        summary="A volumetric or application-layer attack makes the customer portal unavailable for several hours.",
        ratings=(3, 4, 2, 3),
        strategy="transfer",
        status="in_progress",
        plan="Use the hosting provider's managed DDoS protection under the service contract and keep capacity alarms on the edge services.",
        due=30,
        owner="platform_engineer",
        controls={
            "iso_27001_2022": ["A.5.19", "A.8.6", "A.8.14", "A.8.20"],
            "soc2_tsc": ["A1.1"],
        },
        affects=["Customers"],
        vendors=["Example Hosting"],
    ),
    "insider_export": dict(
        title="Insider exports customer records",
        asset="data_store",
        types=[C],
        summary="A staff member with legitimate access exports customer records for an unauthorised purpose.",
        ratings=(2, 4, 1, 4),
        strategy="mitigate",
        status="awaiting_review",
        plan="Least-privilege roles, export logging with alerts, data loss prevention on email and shared drives, and a clear disciplinary process.",
        due=10,
        owner="dpo",
        controls={
            "iso_27001_2022": ["A.5.3", "A.6.4", "A.8.12", "A.8.15", "A.8.16"],
            "soc2_tsc": ["CC6.7"],
        },
        affects=["Customers", "Regulators"],
    ),
    "supplier_breach": dict(
        title="Hosting supplier security breach",
        asset="platform",
        types=[C, AV],
        summary="A breach at the managed hosting supplier exposes shared infrastructure or interrupts service.",
        ratings=(3, 4, 2, 4),
        strategy="transfer",
        status="in_progress",
        plan="Contractual breach notification within 24 hours, an annual assurance review and a documented exit plan.",
        due=90,
        owner="isms_owner",
        controls={
            "iso_27001_2022": ["A.5.19", "A.5.20", "A.5.21", "A.5.22", "A.5.23"],
            "soc2_tsc": ["CC9.2"],
            "pci_dss_v4": ["12.8"],
        },
        affects=["Customers", "Managed hosting provider"],
        vendors=["Example Hosting"],
    ),
    "budget_reduction": dict(
        title="Security roadmap slips after a budget reduction",
        asset="isms_programme",
        types=[],
        summary="A mid-year budget reduction delays planned security tooling and staff training.",
        ratings=(2, 3, 2, 2),
        strategy="accept",
        status="awaiting_review",
        plan="Prioritise the roadmap against the risk register and present the residual risk at the next management review.",
        due=30,
        owner="isms_owner",
        controls={"iso_27001_2022": ["A.5.1", "A.5.4"]},
        affects=["Staff"],
    ),
    "audit_nonconformity": dict(
        title="Certification audit raises a major nonconformity",
        asset="isms_programme",
        types=[],
        summary="Gaps in evidence for priority controls lead the certification auditor to raise a major nonconformity.",
        ratings=(2, 4, 1, 3),
        strategy="mitigate",
        status="in_progress",
        plan="Collect current evidence for 95% of priority controls and close internal audit findings before the stage 2 audit.",
        due=75,
        owner=None,
        controls={
            "iso_27001_2022": ["A.5.35", "A.5.36"],
            "soc2_tsc": ["CC4.1"],
        },
        affects=["Customers"],
    ),
    "supplier_insolvency": dict(
        title="Critical supplier becomes insolvent",
        asset="identity",
        types=[],
        summary="The identity service supplier stops trading, leaving no support or migration route for single sign-on.",
        ratings=(1, 4, 1, 3),
        strategy="mitigate",
        status="open",
        plan="Hold an exit plan with a tested export of directory data and a second supplier assessed for each critical service.",
        due=120,
        owner="isms_owner",
        controls={"iso_27001_2022": ["A.5.19", "A.5.22", "A.5.30"]},
        affects=["Staff"],
        vendors=["Example Identity Services"],
    ),
}


def seed_risks(seeder, assets, org_nodes):
    """Seed the register; returns the risks by key for stories."""
    parties = {
        name.name: party
        for party, name in seeder.db.query(InterestedParty, InterestedPartyName)
        .join(InterestedPartyName, InterestedPartyName.id == InterestedParty.name_id)
        .filter(InterestedPartyName.name.like(seeder.prefix + " %"))
    }
    risks = {}
    for key, spec in RISKS.items():
        owner = org_nodes[spec["owner"]] if spec["owner"] else None
        due = spec["due"]
        risk = seeder.upsert(
            Risk,
            {
                "asset_id": assets[spec["asset"]].id,
                "threat_summary": spec["summary"],
            },
            {
                "title": spec["title"],
                "risk_types": list(spec["types"]),
                "risk_owner_user_id": None if owner else seeder.user.id,
                "risk_owner_role_id": owner.id if owner else None,
                **seeder.risk_ratings(*spec["ratings"]),
                "treatment_strategy": spec["strategy"],
                "treatment_status": spec["status"],
                "treatment_plan": spec["plan"],
                "treatment_due_at": None
                if due is None
                else seeder.seed_date + timedelta(days=due),
                "note": "Fictional demo risk; not a real assessment.",
                "mitigator_context": {"seed": "demo", "prefix": seeder.prefix},
                "created_by_user_id": seeder.user.id,
            },
        )
        for framework, refs in spec["controls"].items():
            seeder.link_risk_controls(risk, refs, framework)
        for name in spec["affects"]:
            party = parties.get(seeder.label(name))
            if party is not None:
                seeder.ensure_link(
                    RiskInterestedParty,
                    {"risk_id": risk.id, "interested_party_id": party.id},
                )
        for name in spec.get("vendors", ()):
            vendor = ensure_vendor(seeder, name)
            seeder.ensure_link(
                RiskInterestedParty, {"risk_id": risk.id, "vendor_id": vendor.id}
            )
        risks[key] = risk
    return risks
