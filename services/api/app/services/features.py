"""Installation-wide availability; permissions remain independently authoritative."""

from dataclasses import dataclass
from threading import RLock
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
from app.services.errors import DomainError

CONFIG_NAME = "features"

# Ordered parents precede children. Keys are stable API identifiers.
DEFINITIONS = (
    ("controls", "Controls workspace", None),
    ("clauses", "Clauses workspace", None),
    ("evidence", "Evidence browsing and downloads", None),
    ("sources", "Sources workspace", None),
    ("visualisations", "Visualisations", None),
    ("explorer", "Connections explorer", None),
    ("risks", "Risk management", None),
    ("risk_cia", "CIA risk assessments", "risks"),
    ("risk_register", "Risk register", "risk_cia"),
    ("mitigator", "Mitigator", "risk_cia"),
    ("pestle", "PESTLE(E)", "risks"),
    ("interested_parties", "Interested parties", "risks"),
    ("isms", "ISMS", None),
    ("objectives", "Objectives", "isms"),
    ("swot", "SWOT analysis", "isms"),
    ("documents", "Policies and processes", "isms"),
    ("organisation", "Organisation", "isms"),
    ("assets", "Assets and licences", "isms"),
    ("business_processes", "Business processes", "isms"),
    ("access_control", "Access control matrix", "isms"),
    ("effectiveness", "Effectiveness measures", "isms"),
    ("meetings", "Meetings", "isms"),
    ("people", "People and personnel assurance", "isms"),
    ("vendors", "Vendors", "isms"),
    ("soa", "Statement of Applicability", "isms"),
    ("audits", "Audits", None),
    ("audit_scheduling", "Scheduled audits", "audits"),
    ("sampling", "Evidence and entity sampling into audits", "audits"),
    ("event_sampling", "Event sampling as PDF or audit evidence", "evidence"),
    ("questions", "Questions and replies", None),
    ("stories", "Stories", None),
    ("incidents", "Incident actions", None),
)
KEYS = frozenset(key for key, _, _ in DEFINITIONS)
ENTITY_FEATURES = {
    "event": "evidence",
    "risk": "risk_cia",
    "pestle_item": "pestle",
    "interested_party": "interested_parties",
    "isms_swot": "swot",
    "isms_objective": "objectives",
    "isms_document": "documents",
    "bookstack_section": "documents",
    "isms_org_node": "organisation",
    "isms_asset": "assets",
    "isms_license": "assets",
    "isms_access_control_matrix": "access_control",
    "isms_application_configuration": "access_control",
    "isms_effectiveness_measure": "effectiveness",
    "isms_effectiveness_metric": "effectiveness",
    "isms_meeting": "meetings",
    "person": "people",
    "vendor": "vendors",
    "objective": "objectives",
    "document": "documents",
    "asset": "assets",
    "effectiveness_measure": "effectiveness",
    "effectiveness_metric": "effectiveness",
    "meeting": "meetings",
    "access_control_matrix": "access_control",
    "application_configuration": "access_control",
    "license": "assets",
    "swot": "swot",
    "org_node": "organisation",
    "business_process": "business_processes",
    "story": "stories",
}
SECTION_FEATURES = {
    "objectives": "objectives",
    "documents": "documents",
    "org": "organisation",
    "assets": "assets",
    "access": "access_control",
    "app": "access_control",
    "effectiveness": "effectiveness",
    "meetings": "meetings",
    "swot": "swot",
    "business_processes": "business_processes",
    "people": "people",
    "vendors": "vendors",
}


@dataclass(frozen=True)
class FeatureState:
    version: int
    configured: tuple
    effective: tuple

    def enabled(self, key):
        if key not in KEYS:
            raise ValueError("Unknown feature")
        return dict(self.effective)[key]

    def require(self, *keys):
        if any(not self.enabled(key) for key in keys):
            raise DomainError(404, "This feature is disabled by an administrator.")

    def public(self):
        return {"version": self.version, "enabled": dict(self.effective)}

    def admin(self):
        return {
            **self.public(),
            "configured": dict(self.configured),
            "items": [
                {"key": key, "label": label, "parent": parent}
                for key, label, parent in DEFINITIONS
            ],
        }


def compile_state(version, document):
    if (
        not isinstance(document, dict)
        or set(document) - KEYS
        or any(type(v) is not bool for v in document.values())
    ):
        raise DomainError(503, "Feature configuration is unavailable.")
    configured = {key: document.get(key, True) for key, _, _ in DEFINITIONS}
    effective = {}
    for key, _, parent in DEFINITIONS:
        effective[key] = configured[key] and (not parent or effective[parent])
    return FeatureState(version, tuple(configured.items()), tuple(effective.items()))


# One immutable document per live database engine. No user identities, secrets,
# or authorisation decisions are cached. Each request checks the DB revision.
_cache = WeakKeyDictionary()
_cache_lock = RLock()


def state(db, *, refresh=False):
    if not refresh and "keen_features" in db.info:
        return db.info["keen_features"]
    version = db.scalar(
        select(ManagedConfiguration.version).where(
            ManagedConfiguration.name == CONFIG_NAME
        )
    )
    engine = db.get_bind()
    with _cache_lock:
        cached = _cache.get(engine)
    if cached is None or cached.version != (version or 0):
        row = (
            db.execute(
                select(
                    ManagedConfiguration.version, ManagedConfiguration.document
                ).where(ManagedConfiguration.name == CONFIG_NAME)
            ).first()
            if version is not None
            else None
        )
        cached = (
            compile_state(row.version, row.document) if row else compile_state(0, {})
        )
        with _cache_lock:
            _cache[engine] = cached
    db.info["keen_features"] = cached
    return cached


def require_entity(db, entity_type):
    key = ENTITY_FEATURES.get(entity_type)
    if key:
        state(db).require(key)


def update(db, user, version, changes):
    if (
        type(version) is not int
        or version < 0
        or not isinstance(changes, dict)
        or not changes
        or set(changes) - KEYS
        or any(type(v) is not bool for v in changes.values())
    ):
        raise DomainError(
            422, "Supply a configuration version and known boolean feature settings."
        )
    row = (
        db.query(ManagedConfiguration)
        .filter_by(name=CONFIG_NAME)
        .with_for_update()
        .one_or_none()
    )
    if (row.version if row else 0) != version:
        raise DomainError(
            409, "Features changed in another session. Reload before saving."
        )
    document = dict(compile_state(version, row.document if row else {}).configured)
    document.update(changes)
    updated = compile_state(version + 1, document)
    if row is None:
        row = ManagedConfiguration(
            name=CONFIG_NAME,
            document=document,
            version=updated.version,
            updated_by=user.id,
        )
        db.add(row)
    else:
        row.document, row.version, row.updated_by = document, updated.version, user.id
    db.add(
        ManagedConfigurationRevision(
            name=CONFIG_NAME,
            document=document,
            version=updated.version,
            updated_by=user.id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DomainError(
            409, "Features changed in another session. Reload before saving."
        ) from None
    db.info.pop("keen_features", None)
    with _cache_lock:
        _cache.pop(db.get_bind(), None)
    return updated.admin()
