"""Fictional PESTLE(E) analysis: three issues per factor, internal and external.

Each item rates its overall relevance and its relevance to the organisation's
business processes and to ISO 27001 clauses, so the matrix, summaries and
clause views all have something to show.
"""

from app.db.models import (
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
)

# (type, lens, issue, rationale, overall relevance,
#  {business process: relevance}, {clause: relevance})
ITEMS = (
    (
        "Political",
        "External",
        "Public-sector cyber assurance expectations increase evidence obligations.",
        "New assurance requirements may require clearer control ownership, documented evidence, and audit-ready reporting.",
        "high",
        {"onboarding": "medium", "service": "medium"},
        {"4.1": "high", "4.2": "high", "6.1": "high"},
    ),
    (
        "Political",
        "External",
        "Government buyers require Cyber Essentials Plus for new contracts.",
        "Public-sector opportunities depend on holding a current certificate, so its scope and renewal date need an owner.",
        "medium",
        {"onboarding": "high", "supplier": "low"},
        {"4.2": "high", "6.2": "medium"},
    ),
    (
        "Political",
        "Internal",
        "A change of board chair shifts executive sponsorship for security.",
        "Leadership commitment and resourcing decisions should be reconfirmed with the new chair at the next management review.",
        "medium",
        {"service": "low"},
        {"5.1": "high", "5.3": "medium", "9.3": "high"},
    ),
    (
        "Economical",
        "External",
        "Supplier price rises pressure tooling and monitoring coverage.",
        "Budget changes may reduce monitoring depth unless control effectiveness is clearly measured.",
        "high",
        {"supplier": "medium", "service": "medium"},
        {"4.2": "high", "6.1": "high", "8.1": "high"},
    ),
    (
        "Economical",
        "External",
        "Cyber insurance premiums depend on demonstrable MFA, backup and patching controls.",
        "Insurers ask for evidence of specific controls at renewal; weak evidence raises the premium or the excess.",
        "high",
        {"supplier": "medium", "service": "high"},
        {"6.1": "high", "6.1.3": "high", "8.3": "medium"},
    ),
    (
        "Economical",
        "Internal",
        "A fixed security budget limits headcount in the operations team.",
        "Automation and clear priorities have to absorb growth in monitoring and response work.",
        "medium",
        {"service": "high", "incident": "medium"},
        {"6.2": "medium", "7.1": "high"},
    ),
    (
        "Social",
        "External",
        "Customers expect clear privacy notices and rapid incident communication.",
        "Customer trust is affected by the speed and transparency of security communication.",
        "high",
        {"onboarding": "medium", "incident": "medium"},
        {"4.2": "high", "7.4": "high", "8.2": "high"},
    ),
    (
        "Social",
        "Internal",
        "Hybrid working increases the use of home networks and shared spaces.",
        "Remote working rules, device management and awareness training must cover working away from the office.",
        "high",
        {"service": "high"},
        {"6.1": "high", "7.3": "high", "8.1": "medium"},
    ),
    (
        "Social",
        "External",
        "A skills shortage makes experienced security staff hard to recruit.",
        "Competence plans combine training, trained deputies and managed services with recruitment.",
        "medium",
        {"incident": "medium", "service": "low"},
        {"7.1": "high", "7.2": "high"},
    ),
    (
        "Technological",
        "Internal",
        "Cloud platform changes introduce configuration drift risk.",
        "Managed hosting changes must be detected and linked to access review, logging, and hardening controls.",
        "high",
        {"service": "medium", "incident": "medium"},
        {"6.1": "high", "8.1": "high", "8.3": "high"},
    ),
    (
        "Technological",
        "External",
        "Generative AI tools raise the quality and volume of phishing campaigns.",
        "Training and technical controls should assume convincing, well-written lures in any language.",
        "high",
        {"incident": "high", "service": "medium"},
        {"6.1": "high", "7.3": "high"},
    ),
    (
        "Technological",
        "Internal",
        "The legacy reporting server reaches the end of vendor support.",
        "Unsupported software stops receiving security updates; replacement must be planned as a controlled change.",
        "medium",
        {"service": "medium"},
        {"6.1": "medium", "6.3": "high", "8.1": "medium"},
    ),
    (
        "Legal",
        "External",
        "Retention and breach-notification obligations require reliable records.",
        "Evidence must support investigation timelines, legal retention, and notification decisions.",
        "high",
        {"incident": "medium", "supplier": "medium"},
        {"4.2": "high", "8.2": "high", "9.1": "high"},
    ),
    (
        "Legal",
        "Internal",
        "Customer contracts for card-handling services must state PCI DSS responsibilities.",
        "Keeping card data in the payment provider's hosted fields limits the scope to validate each year, and each contract says who validates what.",
        "high",
        {"onboarding": "high", "service": "medium"},
        {"4.2": "high", "4.3": "high", "6.1": "medium"},
    ),
    (
        "Legal",
        "External",
        "Proposed cyber resilience legislation extends incident reporting to managed service providers.",
        "Reporting deadlines may shorten; incident procedures and contact lists should be ready before the law takes effect.",
        "medium",
        {"incident": "high"},
        {"4.1": "medium", "4.2": "high", "6.3": "medium"},
    ),
    (
        "Environmental",
        "External",
        "Regional outage or office disruption affects support operations.",
        "Continuity assumptions should be tested through tabletop and restore exercises.",
        "high",
        {"service": "medium", "incident": "medium"},
        {"6.1": "high", "8.1": "high", "8.3": "high"},
    ),
    (
        "Environmental",
        "External",
        "Severe weather makes local power and network outages more likely.",
        "Remote working, battery backup and supplier failover keep support running during local outages.",
        "medium",
        {"service": "medium", "incident": "low"},
        {"6.1": "medium", "8.1": "medium"},
    ),
    (
        "Environmental",
        "Internal",
        "Customers ask for data-centre energy and e-waste reporting.",
        "Supplier questionnaires and secure equipment disposal records can answer both sustainability and security questions.",
        "low",
        {"supplier": "low"},
        {"4.2": "medium", "6.2": "low"},
    ),
    (
        "Ethical",
        "Internal",
        "AI-assisted administration requires transparency and human review.",
        "Use of assistive tooling should not bypass approval, review, or accountability controls.",
        "high",
        {"service": "medium", "supplier": "medium"},
        {"5.1": "high", "5.3": "high", "7.3": "high"},
    ),
    (
        "Ethical",
        "Internal",
        "Employee monitoring must stay proportionate and transparent.",
        "Security logging that covers staff activity should be explained in policy and limited to its stated purpose.",
        "medium",
        {"service": "medium"},
        {"5.2": "high", "7.3": "medium", "7.4": "medium"},
    ),
    (
        "Ethical",
        "External",
        "Security researchers expect a clear and fair vulnerability disclosure route.",
        "A published disclosure policy and a prompt, respectful response protect both the researcher and the service.",
        "medium",
        {"incident": "high"},
        {"4.2": "medium", "7.4": "high", "8.1": "low"},
    ),
)


def seed_pestle(seeder, processes):
    levels = seeder.relevance_levels
    for typ, lens, item, rationale, overall, process_links, clause_links in ITEMS:
        row = seeder.upsert(
            PestleItem,
            {"framework_slug": seeder.framework, "item": seeder.label(item)},
            {
                "type": typ,
                "lens": lens,
                "overall_relevance_id": levels[overall].id,
                "rationale": rationale,
                "created_by_user_id": seeder.user.id,
            },
        )
        for key, relevance in process_links.items():
            seeder.upsert(
                PestleBusinessProcessRelevance,
                {"pestle_item_id": row.id, "business_process_id": processes[key].id},
                {"relevance_id": levels[relevance].id},
            )
        for ref, relevance in clause_links.items():
            clause = seeder._clause(ref)
            if not clause:
                continue
            seeder.upsert(
                PestleClauseRelevance,
                {"pestle_item_id": row.id, "clause_id": clause.id},
                {"relevance_id": levels[relevance].id},
            )
