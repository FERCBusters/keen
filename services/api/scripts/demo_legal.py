"""Example legal register for the demo: United Kingdom laws a fictional
software company would consider, some in scope and some recorded as considered
and out of scope. The wording is illustrative and is not legal advice."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from app.db.models import (
    IsmsEntityControlLink,
    IsmsLegalAssessment,
    IsmsLegalCategory,
    IsmsLegalItem,
    IsmsLegalItemLink,
)
from app.services.isms.legal_register import default_controls
from sqlalchemy import func

UK = "United Kingdom"
LEGISLATION = "https://www.legislation.gov.uk/"
# key, title, link, category, authority, requirement, applicability, in scope,
# the outcome of an assessment made before the seed date (or None), and how
# many days after the seed date the next assessment falls.
ITEMS = (
    (
        "dpa",
        "Data Protection Act 2018",
        LEGISLATION + "ukpga/2018/12/contents",
        "Data protection and privacy",
        "Information Commissioner's Office",
        "Process personal data lawfully, fairly and securely, keep it no longer "
        "than needed, and uphold people's rights over their data.",
        "Applies: the fictional company holds customer and staff personal data.",
        True,
        "partially_compliant",
        -5,
    ),
    (
        "cma",
        "Computer Misuse Act 1990",
        LEGISLATION + "ukpga/1990/18/contents",
        "Information security and computer misuse",
        "Crown Prosecution Service",
        "Makes unauthorised access to, and unauthorised acts impairing, "
        "computers an offence.",
        "Applies: shapes acceptable use, penetration-test authorisation and "
        "how incidents are reported.",
        True,
        "compliant",
        180,
    ),
    (
        "cdpa",
        "Copyright, Designs and Patents Act 1988",
        LEGISLATION + "ukpga/1988/48/contents",
        "Intellectual property",
        "Intellectual Property Office",
        "Protects copyright works, including software and databases, and "
        "their licensing.",
        "Applies: software licences and third-party code are tracked as assets.",
        True,
        "compliant",
        240,
    ),
    (
        "companies",
        "Companies Act 2006",
        LEGISLATION + "ukpga/2006/46/contents",
        "Corporate governance",
        "Companies House / Department for Business and Trade",
        "Sets directors' duties, record keeping, financial reporting and audit "
        "for companies.",
        "Applies: statutory records are information assets with set retention.",
        True,
        "compliant",
        300,
    ),
    (
        "bribery",
        "Bribery Act 2010",
        LEGISLATION + "ukpga/2010/23/contents",
        "Corporate governance",
        "Serious Fraud Office",
        "Makes bribery, and a commercial organisation's failure to prevent "
        "it, an offence.",
        "Applies: suppliers and staff acting for the company are in its reach.",
        True,
        None,
        10,
    ),
    (
        "slavery",
        "Modern Slavery Act 2015",
        LEGISLATION + "ukpga/2015/30/contents",
        "Labour law",
        "Home Office / Independent Anti-Slavery Commissioner",
        "Larger commercial organisations publish a yearly statement on slavery "
        "and trafficking in their business and supply chains.",
        "Considered: the fictional company is below the turnover threshold, "
        "so no statement is required.",
        False,
        None,
        None,
    ),
    (
        "consumer",
        "Consumer Rights Act 2015",
        LEGISLATION + "ukpga/2015/15/contents",
        "Consumer rights",
        "Competition and Markets Authority / Trading Standards",
        "Sets consumers' rights for goods, digital content and services, and "
        "rules on unfair contract terms.",
        "Considered: the fictional company sells to businesses only.",
        False,
        None,
        None,
    ),
    (
        "fsma",
        "Financial Services and Markets Act 2000",
        LEGISLATION + "ukpga/2000/8/contents",
        "Financial regulation",
        "Financial Conduct Authority / Prudential Regulation Authority",
        "Requires authorisation for regulated financial activities and sets "
        "conduct and market-abuse rules.",
        "Considered: the fictional company carries out no regulated activity.",
        False,
        None,
        None,
    ),
    (
        "building",
        "Building Safety Act 2022",
        LEGISLATION + "ukpga/2022/30/contents",
        "Fire safety",
        "Building Safety Regulator (HSE)",
        "Sets duties for the safety of higher-risk buildings through their life.",
        "Considered: the office is leased in a building its landlord manages.",
        False,
        None,
        None,
    ),
    (
        "gpsr",
        "General Product Safety Regulations 2005",
        LEGISLATION + "uksi/2005/1803/contents",
        "Product safety",
        "Office for Product Safety and Standards",
        "Producers and distributors supply only safe consumer products and "
        "act on risks found.",
        "Considered: the fictional company supplies no physical products.",
        False,
        None,
        None,
    ),
)


def _category(seeder, name):
    row = (
        seeder.db.query(IsmsLegalCategory)
        .filter(func.lower(IsmsLegalCategory.name) == name.lower())
        .one_or_none()
    )
    if row is None:
        row = IsmsLegalCategory(name=name, description="")
        seeder.db.add(row)
        seeder.db.flush()
    return row


def seed_legal(seeder, frameworks, docs, assets, risks):
    """Seed the example register; called after documents, assets and risks."""
    db = seeder.db

    def identity(key):
        return uuid5(NAMESPACE_URL, f"keen:demo:legal:{seeder.prefix}:{key}")

    for (
        key,
        title,
        url,
        category,
        authority,
        requirement,
        applicability,
        in_scope,
        outcome,
        next_days,
    ) in ITEMS:
        assessed = seeder.seed_date - timedelta(days=120) if outcome else None
        row = seeder.upsert(
            IsmsLegalItem,
            {"id": identity(key)},
            {
                "title": seeder.label(title),
                "url": url,
                "category_id": _category(seeder, category).id,
                "jurisdiction": UK,
                "authority": authority,
                "requirement": requirement,
                "applicability": applicability,
                "in_scope": in_scope,
                "owner_user_id": seeder.user.id,
                "created_by_user_id": seeder.user.id,
                "compliance_status": outcome or "not_assessed",
                "last_assessed_date": assessed,
                "next_assessment_date": (
                    seeder.seed_date + timedelta(days=next_days)
                    if next_days is not None
                    else None
                ),
            },
        )
        if outcome:
            seeder.upsert(
                IsmsLegalAssessment,
                {"id": identity("assessment-" + key)},
                {
                    "item_id": row.id,
                    "assessed_on": assessed,
                    "assessed_by_user_id": seeder.user.id,
                    "created_by_user_id": seeder.user.id,
                    "outcome": outcome,
                    "notes": "Fictional assessment recorded by the demo seed.",
                },
            )
        if not in_scope:
            continue
        # As a new item in the application: the framework's control for legal
        # requirements, where the framework has one.
        for framework in frameworks:
            for control in default_controls(db, framework):
                seeder.ensure_link(
                    IsmsEntityControlLink,
                    {
                        "entity_type": "legal_item",
                        "entity_id": row.id,
                        "framework_slug": framework,
                        "control_item_id": control["id"],
                    },
                )
    # The data protection item relates to one seeded record of each kind.
    related = (("document", docs), ("asset", assets), ("risk", risks))
    for kind, records in related:
        if records:
            seeder.ensure_link(
                IsmsLegalItemLink,
                {
                    "item_id": identity("dpa"),
                    "target_type": kind,
                    "target_id": next(iter(records.values())).id,
                },
            )
