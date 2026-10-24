"""Feature requirements for matched routes, never a public-route exemption."""

from app.services.features import ENTITY_FEATURES, SECTION_FEATURES

OWNERS = {
    "controls": "controls",
    "control_links": "controls",
    "clauses": "clauses",
    "events": "evidence",
    "artifacts": "evidence",
    "sources": "sources",
    "graph": "visualisations",
    "home": "explorer",
    "audits": "audits",
    "questions": "questions",
    "risks": "risk_cia",
    "risk_register": "risk_register",
    "pestle": "pestle",
    "interested_parties": "interested_parties",
    "bookstack_sections": "documents",
    "soa": "soa",
    "stories": "stories",
    "quizzes": "quizzes",
}
ISMS_OWNERS = {
    "objectives": "objectives",
    "swot": "swot",
    "documents": "documents",
    "organisation": "organisation",
    "assets": "assets",
    "business_processes": "business_processes",
    "access_control": "access_control",
    "effectiveness": "effectiveness",
    "meetings": "meetings",
}


def requirements(route, params=None, query=None):
    """Use the router's resolved owner and complete path segments.

    Unknown entity types are still rejected by the operation's own validator.
    This boundary adds availability checks; it never grants permissions.
    """
    params, query = params or {}, query or {}
    module = route.endpoint.__module__.removeprefix("app.api.routes.")
    owner = module.split(".")[0]
    parts = route.path.strip("/").split("/")
    needed = set()
    if owner in OWNERS:
        needed.add(OWNERS[owner])
    if owner == "isms":
        needed.add("isms")
        child = ISMS_OWNERS.get(module.split(".")[-1])
        if child:
            needed.add(child)
        personal = {
            "isms-objectives": "objectives",
            "isms-assets": "assets",
            "isms-meetings": "meetings",
            "isms-role": "organisation",
        }
        needed.update(personal[p] for p in parts if p in personal)
        section = SECTION_FEATURES.get(query.get("section"))
        if section:
            needed.add(section)
    if owner == "assurance":
        needed.add("isms")
        needed.update(p for p in ("people", "vendors") if p in parts)
    if "scheduled" in parts and owner == "audits":
        needed.add("audit_scheduling")
    if "mitigator" in parts and owner == "risks":
        needed.add("mitigator")
    if owner == "controls" and "evidence" in parts:
        needed.add("evidence")
    if owner == "questions" and "events" in parts:
        needed.add("evidence")
    if owner == "stats" and "charts" in parts:
        needed.add("visualisations")
    if owner == "events" and any(p in parts for p in ("incident", "incidents")):
        needed.add("incidents")
    if owner == "graph" and parts[-1] == "risk_control":
        needed.add("risk_cia")
    if "effectiveness-measures" in parts:
        needed.add("effectiveness")
    for name in ("entity_type", "target_type"):
        value = str(params.get(name) or "").strip().lower().replace("-", "_")
        feature = ENTITY_FEATURES.get(value)
        if feature:
            needed.add(feature)
    return frozenset(needed)
