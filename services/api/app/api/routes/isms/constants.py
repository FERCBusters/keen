"""ISMS constants; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

from app.db.models import (
    IsmsAccessControlMatrixEntry,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsLicense,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    RiskAsset,
)

ISMS_READ_PERMISSION = "isms.read"


ISMS_MANAGE_PERMISSION = "isms.manage"


RISK_READ_PERMISSION = "risk.read"


RISK_MANAGE_PERMISSION = "risk.manage"


DOCUMENT_TYPES = {
    "policy",
    "process",
    "procedure",
    "standard",
    "guideline",
    "record",
    "other",
}


OBJECTIVE_STATUSES = {
    "not_started",
    "in_progress",
    "completed",
    "deferred",
    "superseded",
}


APP_SOURCE_TYPES = {"document", "person", "asset", "org_node"}


ACCESS_STATUSES = {"Pending Approval", "Approved"}


MEETING_LINK_TYPES = {"isms_document", "external_url"}


EFFECTIVENESS_THRESHOLD_OPERATORS = {"", "lt", "lte", "eq", "gte", "gt"}


EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE = "other"


ISMS_SECTION_ALIASES = {
    "overview": "overview",
    "summary": "overview",
    "objective": "objectives",
    "objectives": "objectives",
    "document": "documents",
    "documents": "documents",
    "policies": "documents",
    "policy": "documents",
    "processes": "documents",
    "org": "org",
    "org_node": "org",
    "org_nodes": "org",
    "org-nodes": "org",
    "organisation": "org",
    "organization": "org",
    "assets": "assets",
    "asset": "assets",
    "access": "access",
    "accesscontrol": "access",
    "access_control": "access",
    "access-control": "access",
    "access_control_matrix": "access",
    "access-control-matrix": "access",
    "effectiveness": "effectiveness",
    "effectiveness_measure": "effectiveness",
    "effectiveness-measure": "effectiveness",
    "effectiveness_measures": "effectiveness",
    "effectiveness-measures": "effectiveness",
    "metrics": "effectiveness",
    "meetings": "meetings",
    "meeting": "meetings",
    "soa": "soa",
}


ENTITY_TYPES = {
    "objective": (IsmsObjective, "isms_objective"),
    "document": (IsmsDocument, "isms_document"),
    "org_node": (IsmsOrgNode, "isms_org_node"),
    "asset": (RiskAsset, "isms_asset"),
    "license": (IsmsLicense, "isms_license"),
    "access_control_matrix": (
        IsmsAccessControlMatrixEntry,
        "isms_access_control_matrix",
    ),
    "meeting": (IsmsMeeting, "isms_meeting"),
    "effectiveness_measure": (
        IsmsEffectivenessMeasure,
        "isms_effectiveness_measure",
    ),
    "effectiveness_metric": (
        IsmsEffectivenessMetricEntry,
        "isms_effectiveness_metric",
    ),
}
