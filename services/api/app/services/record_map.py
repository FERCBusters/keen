"""The Map: the records Keen holds and how they relate, as one graph.

A node is a record: a control, a clause, a risk, a document and so on. A link
is a relationship one of them keeps to another. Every node is a record the
reader could open on its own page, with the feature that owns it switched on,
and a link is drawn only between two such records, so the Map never shows more
than the records' own pages would.

The overview leaves out events, which number in the tens of thousands, and the
controls and clauses nothing refers to. Evidence sources join it on request,
weighted by the events they supply to each control, and a record's neighbours
can be read one record at a time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from mosp.core.identifiers import try_uuid
from mosp.services.context import ChangeContext, signed_in_user
from mosp.services.errors import DomainError
from sqlalchemy import false, or_, select, true
from sqlalchemy.orm import Session

from app.db.models import (
    AffectedLink,
    Audit,
    AuditAttendee,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    BookStackSectionEvidence,
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLegalItem,
    IsmsLegalItemLink,
    IsmsMeeting,
    IsmsMeetingLink,
    IsmsMeetingPerson,
    IsmsObjective,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsPerson,
    IsmsPersonAsset,
    IsmsSwot,
    IsmsSwotLink,
    IsmsVendor,
    PestleClauseRelevance,
    PestleControlRelevance,
    PestleItem,
    Risk,
    RiskAsset,
    RiskClauseLink,
    RiskControlLink,
    Story,
    StoryClauseLink,
    StoryControlLink,
    StoryItem,
)
from app.security.permissions import can_read_events
from app.services.audits.access import attends, can_read_audits
from app.services.event_filters import EventFilters
from app.services.features import state as feature_state
from app.services.frameworks import framework_parameter
from app.services.graph import graph_control_source
from app.services.isms.access import can_read_isms, meeting_ids_for
from app.services.pestle import can_read_pestle, rated_links
from app.services.risks import can_read_risks
from app.services.stories import can_read_stories

# A graph larger than this is cut to its best connected records. No more
# records of one kind, no more rows of one relationship table and no more
# relationship rows in all are read than these, so the work one request
# causes is bounded by them and not by how much the installation holds.
MAX_NODES = 2000
MAX_LINK_ROWS = 20000
MAX_REQUEST_ROWS = 60000
# Counting the events behind the evidence is given this long.
EVIDENCE_BUDGET_MS = 20000
LABEL_LIMIT = 80


@dataclass(frozen=True)
class Kind:
    """A kind of record: its name, the features it needs and its page."""

    key: str
    label: str
    features: tuple[str, ...]
    page: str


KINDS = (
    Kind("control", "Controls", ("controls",), "/control.html?id={id}"),
    Kind("clause", "Clauses", ("clauses",), "/clause.html?id={id}"),
    Kind("risk", "Risks", ("risk_cia",), "/risk.html?id={id}"),
    Kind("pestle_item", "PESTLE(E) items", ("pestle",), "/pestle_item.html?id={id}"),
    Kind(
        "document",
        "Policies and processes",
        ("documents",),
        "/isms-document.html?id={id}",
    ),
    Kind("objective", "Objectives", ("objectives",), "/isms-objective.html?id={id}"),
    Kind("asset", "Assets", ("assets",), "/isms-asset.html?id={id}"),
    Kind("person", "People", ("people",), "/person.html?id={id}"),
    Kind("vendor", "Vendors / 3rd parties", ("vendors",), "/vendor.html?id={id}"),
    Kind("swot", "SWOT items", ("swot",), "/swot.html?id={id}"),
    Kind(
        "legal_item",
        "Legal register",
        ("legal_register",),
        "/legal-register.html?id={id}",
    ),
    Kind(
        "effectiveness_measure",
        "Effectiveness measures",
        ("effectiveness",),
        "/isms-effectiveness-measure.html?id={id}",
    ),
    Kind("meeting", "Meetings", ("meetings",), "/isms-meeting.html?id={id}"),
    Kind("org_node", "Organisation", ("organisation",), "/isms.html?tab=org"),
    Kind("story", "Stories", ("stories",), "/story.html?id={id}"),
    Kind("audit", "Audits", ("audits",), "/audit.html?id={id}"),
    Kind(
        "source",
        "Evidence sources",
        ("sources", "evidence"),
        "/source.html?source={id}",
    ),
)
BY_KEY = {kind.key: kind for kind in KINDS}
# Controls and clauses are the framework's catalogue: the overview shows the
# ones a record refers to, and a record's neighbours show the rest.
CATALOGUE = frozenset({"control", "clause"})


def _short(text: Any, fallback: str) -> str:
    value = " ".join(str(text or "").split()) or fallback
    return value if len(value) <= LABEL_LIMIT else value[: LABEL_LIMIT - 1] + "…"


class _Graph:
    """The readable records and the links between them."""

    def __init__(self, db: Session, context: ChangeContext, framework: str):
        self.db = db
        self.context = context
        self.user = signed_in_user(context)
        self.framework = framework
        self.nodes: dict[str, dict[str, Any]] = {}
        self.links: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.notes: list[str] = []
        self.truncated = False
        self.rows_left = MAX_REQUEST_ROWS
        features = feature_state(db)
        isms = can_read_isms(db, self.user)
        readable = {
            "control": True,
            "clause": True,
            "risk": True,  # Each risk is checked: an owner reads their own.
            "pestle_item": can_read_pestle(db, self.user),
            "meeting": True,  # A person reads the meetings they are on.
            "story": can_read_stories(db, self.user),
            "audit": True,  # An attendee reads the audits they attend.
            "source": can_read_events(db, self.user),
        }
        self.kinds = [
            kind
            for kind in KINDS
            if all(features.enabled(key) for key in kind.features)
            and readable.get(kind.key, isms)
        ]
        self.allowed = {kind.key for kind in self.kinds}
        self.reads_isms = isms

    # --- Records ---------------------------------------------------------------

    def node(self, kind: str, record_id: Any, label: Any, detail: Any = "") -> None:
        key = f"{kind}:{record_id}"
        self.nodes[key] = {
            "id": key,
            "kind": kind,
            "label": _short(label, BY_KEY[kind].label),
            "detail": _short(detail, ""),
            "url": BY_KEY[kind].page.format(id=record_id),
        }

    def link(self, a: tuple[str, Any], b: tuple[str, Any], relation: str, value=1):
        """Relate two records when the reader sees both.

        A link to anything that is not a node (a kind the Map does not draw,
        a record that is withheld or gone, no record at all) is left out.
        """
        source, target = f"{a[0]}:{a[1]}", f"{b[0]}:{b[1]}"
        if source in self.nodes and target in self.nodes and source != target:
            self.links.setdefault(
                (source, target, relation),
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "value": int(value),
                },
            )

    def _capped(self, query, limit: int) -> list:
        """At most ``limit`` of the query's rows; more than that is noted."""
        rows = query.limit(limit + 1).all()
        if len(rows) > limit:
            self.truncated = True
        return rows[:limit]

    def _link_rows(self, query) -> list:
        """A relationship table's rows, within the table's limit and what the
        request has left of its own."""
        rows = self._capped(query, max(0, min(MAX_LINK_ROWS, self.rows_left)))
        self.rows_left -= len(rows)
        return rows

    def _rows(self, kind: str, query) -> list:
        return self._capped(query, MAX_NODES) if kind in self.allowed else []

    def _own_risks(self):
        """The condition for the risks a person reads without risk.read: those
        they own, themselves or through a role. A connection reads none so."""
        if getattr(self.user, "scope_limited", False):
            return false()
        roles = select(IsmsOrgNodeUser.org_node_id).where(
            IsmsOrgNodeUser.user_id == self.user.id
        )
        return or_(
            Risk.risk_owner_user_id == self.user.id,
            Risk.risk_owner_role_id.in_(roles),
        )

    def _risks_read(self):
        """The condition for the risks the reader sees."""
        return true() if can_read_risks(self.db, self.user) else self._own_risks()

    def _audits_read(self) -> list:
        """The conditions for the audits the reader sees: the selected
        framework's, and without audit access only those they attend."""
        conditions = [Audit.framework_slug == self.framework]
        if not can_read_audits(self.db, self.user):
            conditions.append(
                Audit.id.in_(select(AuditAttendee.audit_id).where(attends(self.user)))
            )
        return conditions

    def load_records(self) -> None:
        db, fw = self.db, self.framework
        # Only the columns a node shows are read, never a record's body.
        for row in self._rows(
            "control",
            db.query(ControlItem.id, ControlItem.ref, ControlItem.title).filter(
                ControlItem.framework_slug == fw
            ),
        ):
            self.node("control", row.id, row.ref, row.title)
        for row in self._rows(
            "clause",
            db.query(
                FrameworkClause.id, FrameworkClause.ref, FrameworkClause.title
            ).filter(FrameworkClause.framework_slug == fw),
        ):
            self.node("clause", row.id, row.ref, row.title)
        risks = db.query(Risk.id, Risk.threat_summary, RiskAsset.name).outerjoin(
            RiskAsset, RiskAsset.id == Risk.asset_id
        )
        risks = risks.filter(self._risks_read())
        for risk_id, threat, asset in self._rows("risk", risks):
            self.node("risk", risk_id, threat or asset, asset)
        for row in self._rows(
            "pestle_item",
            db.query(
                PestleItem.id, PestleItem.item, PestleItem.type, PestleItem.lens
            ).filter(PestleItem.framework_slug == fw),
        ):
            self.node("pestle_item", row.id, row.item, f"{row.type} · {row.lens}")
        for row in self._rows(
            "document",
            db.query(IsmsDocument.id, IsmsDocument.title, IsmsDocument.document_type),
        ):
            self.node("document", row.id, row.title, row.document_type)
        for row in self._rows(
            "objective",
            db.query(IsmsObjective.id, IsmsObjective.goal, IsmsObjective.requirement),
        ):
            self.node("objective", row.id, row.goal or row.requirement)
        for row in self._rows("asset", db.query(RiskAsset.id, RiskAsset.name)):
            self.node("asset", row.id, row.name)
        for row in self._rows(
            "person", db.query(IsmsPerson.id, IsmsPerson.name, IsmsPerson.position)
        ):
            self.node("person", row.id, row.name, row.position)
        for row in self._rows("vendor", db.query(IsmsVendor.id, IsmsVendor.name)):
            self.node("vendor", row.id, row.name)
        for row in self._rows(
            "swot", db.query(IsmsSwot.id, IsmsSwot.title, IsmsSwot.kind)
        ):
            self.node("swot", row.id, row.title, row.kind)
        for row in self._rows(
            "legal_item",
            db.query(IsmsLegalItem.id, IsmsLegalItem.title, IsmsLegalItem.jurisdiction),
        ):
            self.node("legal_item", row.id, row.title, row.jurisdiction)
        for row in self._rows(
            "effectiveness_measure",
            db.query(
                IsmsEffectivenessMeasure.id,
                IsmsEffectivenessMeasure.summary,
                IsmsEffectivenessMeasure.metric_key,
            ).filter(IsmsEffectivenessMeasure.framework_slug == fw),
        ):
            self.node("effectiveness_measure", row.id, row.summary or row.metric_key)
        meetings = db.query(IsmsMeeting.id, IsmsMeeting.title, IsmsMeeting.date)
        if not self.reads_isms:
            meetings = meetings.filter(
                IsmsMeeting.id.in_(select(meeting_ids_for(self.user).subquery()))
            )
        for row in self._rows("meeting", meetings):
            self.node("meeting", row.id, row.title or "Meeting minutes", row.date)
        for row in self._rows(
            "org_node",
            db.query(IsmsOrgNode.id, IsmsOrgNode.name, IsmsOrgNode.node_type),
        ):
            self.node("org_node", row.id, row.name, row.node_type)
        for row in self._rows("story", db.query(Story.id, Story.title, Story.status)):
            self.node("story", row.id, row.title, row.status)
        audits = db.query(Audit.id, Audit.title, Audit.audit_type).filter(
            *self._audits_read()
        )
        for row in self._rows("audit", audits):
            self.node("audit", row.id, row.title, row.audit_type)

    # --- Relationships ---------------------------------------------------------

    def _relate(
        self,
        rows: Any,
        a: tuple[str, str],
        b: tuple[str, str],
        relation: str,
        *,
        a_kind_column: str | None = None,
        b_kind_column: str | None = None,
    ) -> None:
        """Link each row's two records.

        ``a`` and ``b`` are each end's kind and the column that holds its id;
        where a row names an end's kind itself, the kind is read from the
        ``..._kind_column`` instead.
        """
        # A table with an end the reader sees none of has no link to give, so
        # it is not read and spends none of the request's allowance.
        if not {a[0], b[0]} - {""} <= self.allowed:
            return
        if not isinstance(rows, list):
            rows = self._link_rows(rows)
        for row in rows:
            a_kind = getattr(row, a_kind_column) if a_kind_column else a[0]
            b_kind = getattr(row, b_kind_column) if b_kind_column else b[0]
            self.link(
                (a_kind, getattr(row, a[1])), (b_kind, getattr(row, b[1])), relation
            )

    def load_links(self) -> None:
        db, fw = self.db, self.framework
        control = ("control", "control_item_id")
        clause = ("clause", "clause_id")
        seen = sorted(self.allowed)
        # Relationships are read for the audits the reader sees and no others.
        audits = select(Audit.id).where(*self._audits_read())
        # (rows, one end, the other end, relation), for the tables whose
        # columns say which kinds they join.
        plain: list[tuple[Any, tuple[str, str], tuple[str, str], str]] = [
            (
                db.query(ControlClauseLink).filter(
                    ControlClauseLink.control_item_id.in_(
                        select(ControlItem.id).where(ControlItem.framework_slug == fw)
                    )
                ),
                clause,
                control,
                "applies",
            ),
            (
                db.query(RiskControlLink).filter_by(framework_slug=fw),
                ("risk", "risk_id"),
                control,
                "mitigates",
            ),
            (
                db.query(RiskClauseLink).filter_by(framework_slug=fw),
                ("risk", "risk_id"),
                clause,
                "mitigates",
            ),
            (
                db.query(BookStackSectionEvidence),
                ("document", "document_id"),
                ("control", "target_control_id"),
                "mapped",
            ),
            (
                db.query(BookStackSectionEvidence),
                ("document", "document_id"),
                ("clause", "target_clause_id"),
                "mapped",
            ),
            (
                db.query(Risk.id, Risk.asset_id),
                ("risk", "id"),
                ("asset", "asset_id"),
                "asset",
            ),
            (
                db.query(IsmsSwot.id, IsmsSwot.parent_id),
                ("swot", "id"),
                ("swot", "parent_id"),
                "part",
            ),
            (
                db.query(RiskAsset.id, RiskAsset.vendor_id),
                ("asset", "id"),
                ("vendor", "vendor_id"),
                "supplied",
            ),
            (
                db.query(RiskAsset.id, RiskAsset.owner_org_node_id),
                ("asset", "id"),
                ("org_node", "owner_org_node_id"),
                "owned",
            ),
            (
                db.query(IsmsPersonAsset),
                ("person", "person_id"),
                ("asset", "asset_id"),
                "uses",
            ),
            (
                db.query(IsmsPerson.id, IsmsPerson.org_node_id),
                ("person", "id"),
                ("org_node", "org_node_id"),
                "member",
            ),
            (
                db.query(IsmsOrgNode.id, IsmsOrgNode.parent_id),
                ("org_node", "id"),
                ("org_node", "parent_id"),
                "part",
            ),
            (
                db.query(IsmsMeetingLink),
                ("meeting", "meeting_id"),
                ("document", "document_id"),
                "refers",
            ),
            (
                db.query(IsmsMeetingPerson),
                ("meeting", "meeting_id"),
                ("person", "person_id"),
                "attends",
            ),
            (
                db.query(StoryControlLink).filter_by(framework_slug=fw),
                ("story", "story_id"),
                control,
                "mapped",
            ),
            (
                db.query(StoryClauseLink).filter_by(framework_slug=fw),
                ("story", "story_id"),
                clause,
                "mapped",
            ),
            (
                db.query(AuditScopedControl).filter(
                    AuditScopedControl.audit_id.in_(audits)
                ),
                ("audit", "audit_id"),
                control,
                "scope",
            ),
            (
                db.query(AuditScopedClause).filter(
                    AuditScopedClause.audit_id.in_(audits)
                ),
                ("audit", "audit_id"),
                clause,
                "scope",
            ),
            (
                db.query(AuditScopedIsmsDocument).filter(
                    AuditScopedIsmsDocument.audit_id.in_(audits)
                ),
                ("audit", "audit_id"),
                ("document", "document_id"),
                "scope",
            ),
            (
                db.query(AuditSwotScope).filter(AuditSwotScope.audit_id.in_(audits)),
                ("audit", "audit_id"),
                ("swot", "swot_id"),
                "scope",
            ),
            (
                db.query(AuditFinding.audit_id, AuditFinding.control_item_id).filter(
                    AuditFinding.audit_id.in_(audits)
                ),
                ("audit", "audit_id"),
                control,
                "finding",
            ),
            (
                db.query(AuditAttendee.audit_id, AuditAttendee.person_id).filter(
                    AuditAttendee.audit_id.in_(audits)
                ),
                ("audit", "audit_id"),
                ("person", "person_id"),
                "attends",
            ),
        ]
        for rows, a, b, relation in plain:
            self._relate(rows, a, b, relation)
        # Tables whose rows name the kind of record at one end.
        self._relate(
            db.query(IsmsEntityControlLink)
            .filter_by(framework_slug=fw)
            .filter(IsmsEntityControlLink.entity_type.in_(seen)),
            ("", "entity_id"),
            control,
            "mapped",
            a_kind_column="entity_type",
        )
        self._relate(
            db.query(IsmsEntityClauseLink)
            .filter_by(framework_slug=fw)
            .filter(IsmsEntityClauseLink.entity_type.in_(seen)),
            ("", "entity_id"),
            clause,
            "mapped",
            a_kind_column="entity_type",
        )
        self._relate(
            db.query(IsmsSwotLink).filter(IsmsSwotLink.target_type.in_(seen)),
            ("swot", "swot_id"),
            ("", "target_id"),
            "related",
            b_kind_column="target_type",
        )
        self._relate(
            db.query(IsmsLegalItemLink).filter(IsmsLegalItemLink.target_type.in_(seen)),
            ("legal_item", "item_id"),
            ("", "target_id"),
            "related",
            b_kind_column="target_type",
        )
        # A story's notes and events are not records the Map draws.
        self._relate(
            db.query(StoryItem.story_id, StoryItem.entity_id, StoryItem.kind).filter(
                StoryItem.kind.in_(seen)
            ),
            ("story", "story_id"),
            ("", "entity_id"),
            "story",
            b_kind_column="kind",
        )
        # Last, the tables read whole before their rows are sorted into links,
        # so they cannot leave the ones above short.
        items = select(PestleItem.id).where(PestleItem.framework_slug == fw)
        if "pestle_item" in self.allowed:
            for model, end in (
                (PestleControlRelevance, control),
                (PestleClauseRelevance, clause),
            ):
                rated = rated_links(
                    self._link_rows(
                        db.query(model).filter(model.pestle_item_id.in_(items))
                    )
                )
                self._relate(rated, ("pestle_item", "pestle_item_id"), end, "relevant")
        if {"person", "vendor", "asset"} & self.allowed:
            # Only what the reader's risks and PESTLE(E) items affect.
            owners = [
                AffectedLink.risk_id.in_(select(Risk.id).where(self._risks_read()))
            ]
            if "pestle_item" in self.allowed:
                owners.append(AffectedLink.pestle_item_id.in_(items))
            affected = self._link_rows(db.query(AffectedLink).filter(or_(*owners)))
            for owner in (("risk", "risk_id"), ("pestle_item", "pestle_item_id")):
                for target in ("person", "vendor", "asset"):
                    self._relate(affected, owner, (target, f"{target}_id"), "affects")

    def load_evidence(self, *, control: str | None = None, source: str | None = None):
        """Add the sources that supply evidence, weighted by their events.

        The events are counted as the Visualisations page counts them, with the
        reader's own visibility; a count that cannot be made is noted.
        """
        if "source" not in self.allowed:
            return
        scope = EventFilters(
            framework=self.framework,
            controls=(control,) if control else (),
            sources=(source,) if source else (),
        )
        try:
            data = graph_control_source(
                context=self.context,
                framework=self.framework,
                scope=scope,
                within_ms=EVIDENCE_BUDGET_MS,
                max_links=MAX_LINK_ROWS,
                only_control=try_uuid(control) if control else None,
                guidance=False,
                db=self.db,
            )
        except DomainError as exc:
            self.notes.append(f"Evidence sources are not shown: {exc.detail}")
            return
        for node in data["nodes"]:
            if node["node_kind"] == "source":
                self.node(
                    "source",
                    node["source"],
                    node["label"],
                    f"{node['mapped_events']} mapped events",
                )
        # Events chosen for one control also count towards the others they map
        # to; only that control's own count is whole, so only it is asked for
        # and shown. Evidence for a control the Map does not hold cannot be
        # drawn, and is set aside before the allowance is applied.
        links = [
            link
            for link in data["links"]
            if control in (None, link["target"].removeprefix("control:"))
            and link["target"] in self.nodes
        ]
        # Evidence counts towards the request's relationships like any other;
        # when there is more than it has left, the heaviest is kept.
        links.sort(key=lambda link: -link["value"])
        if data["truncated"] or len(links) > self.rows_left:
            self.truncated = True
        links = links[: max(0, self.rows_left)]
        self.rows_left -= len(links)
        for link in links:
            self.link(
                ("source", link["source"].removeprefix("source:")),
                ("control", link["target"].removeprefix("control:")),
                "evidence",
                link["value"],
            )

    # --- Output ----------------------------------------------------------------

    def output(self, keep: set[str], *, reach: bool = False) -> dict[str, Any]:
        """The kept records with the links between them.

        With ``reach``, a link from a kept record to any other is included too,
        so a page can join the records to those it already shows.
        """
        within = any if reach else all
        links = [
            link
            for link in self.links.values()
            if within(end in keep for end in (link["source"], link["target"]))
        ]
        nodes = [node for key, node in self.nodes.items() if key in keep]
        degree: dict[str, int] = {}
        for link in links:
            for end in (link["source"], link["target"]):
                degree[end] = degree.get(end, 0) + 1
        truncated = self.truncated or len(nodes) > MAX_NODES
        if len(nodes) > MAX_NODES:
            nodes.sort(key=lambda node: (-degree.get(node["id"], 0), node["id"]))
            nodes = nodes[:MAX_NODES]
            kept = {node["id"] for node in nodes}
            links = [
                link
                for link in links
                if within(end in kept for end in (link["source"], link["target"]))
            ]
        counts: dict[str, int] = {}
        for node in nodes:
            counts[node["kind"]] = counts.get(node["kind"], 0) + 1
        return {
            "framework": self.framework,
            "nodes": nodes,
            "links": links,
            "kinds": [
                {"key": kind.key, "label": kind.label, "count": counts.get(kind.key, 0)}
                for kind in self.kinds
            ],
            "truncated": truncated,
            "notes": self.notes,
        }


def _graph(db: Session, context: ChangeContext, framework: str | None) -> _Graph:
    graph = _Graph(db, context, framework_parameter(framework))
    graph.load_records()
    graph.load_links()
    return graph


def overview(*, context: ChangeContext, framework, evidence: bool = False, db: Session):
    """Every readable record, and the controls and clauses they refer to."""
    graph = _graph(db, context, framework)
    if evidence:
        graph.load_evidence()
    referred: set[str] = set()
    for link in graph.links.values():
        ends = (link["source"], link["target"])
        kinds = [end.split(":", 1)[0] for end in ends]
        if not CATALOGUE.issuperset(kinds):
            referred.update(ends)
    keep = {
        key
        for key, node in graph.nodes.items()
        if node["kind"] not in CATALOGUE or key in referred
    }
    return graph.output(keep)


def neighbours(*, context: ChangeContext, framework, node: str, db: Session):
    """One record with the records related to it, catalogue and evidence included.

    The links are the record's own and its neighbours', so the page can also
    join a neighbour to the records it already shows.
    """
    kind, _, record_id = str(node or "").partition(":")
    if kind not in BY_KEY or not record_id:
        raise DomainError(400, "node names a kind of record and its id")
    if kind != "source" and not isinstance(try_uuid(record_id), uuid.UUID):
        raise DomainError(400, "node names a kind of record and its id")
    graph = _Graph(db, context, framework_parameter(framework))
    graph.load_records()
    # A record that is not there, or not the reader's to open, is answered
    # before any relationship is read.
    if kind != "source" and node not in graph.nodes:
        raise DomainError(404, "Record not available")
    graph.load_links()
    if kind == "source":
        graph.load_evidence(source=record_id)
        if node not in graph.nodes:
            raise DomainError(404, "Record not available")
    elif kind == "control":
        graph.load_evidence(control=record_id)
    keep = {node}
    for link in graph.links.values():
        if node in (link["source"], link["target"]):
            keep.update((link["source"], link["target"]))
    return graph.output(keep, reach=True)
