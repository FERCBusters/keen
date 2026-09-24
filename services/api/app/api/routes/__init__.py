from __future__ import annotations

from fastapi import APIRouter

from .admin import router as admin_router
from .artifacts import router as artifacts_router
from .auth import router as auth_router
from .audits import router as audits_router
from .clauses import router as clauses_router
from .controls import router as controls_router
from .control_links import router as control_links_router
from .events import router as events_router
from .frameworks import router as frameworks_router
from .managed_configurations import router as managed_configurations_router
from .graph import router as graph_router
from .interested_parties import router as interested_parties_router
from .isms import router as isms_router
from .assurance import router as assurance_router
from .bookstack_sections import router as bookstack_sections_router
from .me import router as me_router
from .questions import router as questions_router
from .pestle import router as pestle_router
from .risks import router as risks_router
from .risk_register import router as risk_register_router
from .sources import router as sources_router
from .soa import router as soa_router
from .stats import router as stats_router
from .users import router as users_router
from .webhooks import router as webhooks_router

OPENAPI_TAGS = [
    {"name": "Auth", "description": "Authentication and session endpoints."},
    {
        "name": "Me",
        "description": "Current-user profile, password, and preference endpoints.",
    },
    {"name": "Users", "description": "User administration endpoints."},
    {"name": "Admin", "description": "Administrative and audit endpoints."},
    {"name": "Webhooks", "description": "Inbound webhook endpoints."},
    {"name": "Controls", "description": "Source controls and connector settings."},
    {"name": "Clauses", "description": "Framework clauses and control applicability."},
    {
        "name": "Frameworks",
        "description": "Compliance framework catalogue and metadata.",
    },
    {"name": "Events", "description": "Event and timeline endpoints."},
    {"name": "Questions", "description": "Q&A and event question endpoints."},
    {
        "name": "Risks",
        "description": "CIA Triad risk register, categories and risk/control mappings.",
    },
    {
        "name": "PESTLE(E)",
        "description": "PESTLE(E) impact items, business processes and relevance mappings.",
    },
    {
        "name": "Interested Parties",
        "description": "Interested party names, nature of interest, linked controls and communications.",
    },
    {
        "name": "Artifacts",
        "description": "Artifact listing/detail/rendering endpoints.",
    },
    {
        "name": "Sources",
        "description": "Source registry and source metadata endpoints.",
    },
    {
        "name": "ISMS",
        "description": "ISMS objectives, documents, organisation chart, assets, application configuration matrix, meetings and Statement of Applicability.",
    },
    {"name": "Assurance", "description": "People, vendor and personnel assurance records."},
    {
        "name": "Statement of Applicability",
        "description": "Read-only SoA summary across controls, clauses, CIA risks, PESTLE(E), Interested Parties and ISMS.",
    },
    {"name": "Stats", "description": "Aggregate statistics and counts."},
    {"name": "Graph", "description": "Graph/topology endpoints."},
]

router = APIRouter()

# Keep URL paths stable by including subrouters with no prefix.
router.include_router(auth_router, tags=["Auth"])
router.include_router(audits_router, tags=["Audits"])
router.include_router(me_router, tags=["Me"])
router.include_router(users_router, tags=["Users"])
router.include_router(admin_router, tags=["Admin"])
router.include_router(webhooks_router, tags=["Webhooks"])
router.include_router(controls_router, tags=["Controls"])
router.include_router(control_links_router, tags=["Controls"])
router.include_router(clauses_router, tags=["Clauses"])
router.include_router(frameworks_router, tags=["Frameworks"])
router.include_router(managed_configurations_router, tags=["Admin"])
router.include_router(events_router, tags=["Events"])
router.include_router(questions_router, tags=["Questions"])
router.include_router(pestle_router, tags=["PESTLE(E)"])
router.include_router(interested_parties_router, tags=["Interested Parties"])
router.include_router(isms_router, tags=["ISMS"])
router.include_router(assurance_router, tags=["Assurance"])
router.include_router(bookstack_sections_router, tags=["ISMS"])
router.include_router(risk_register_router, tags=["Risks"])
router.include_router(risks_router, tags=["Risks"])
router.include_router(artifacts_router, tags=["Artifacts"])
router.include_router(sources_router, tags=["Sources"])
router.include_router(soa_router, tags=["Statement of Applicability"])
router.include_router(stats_router, tags=["Stats"])
router.include_router(graph_router, tags=["Graph"])
