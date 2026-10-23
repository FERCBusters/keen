#!/usr/bin/env python3
"""Remove Keen demonstration data created by seed_demo_data.py.

Intended location:
    services/api/scripts/teardown_demo_data.py

Typical use:
    docker compose exec api python scripts/teardown_demo_data.py --dry-run
    docker compose exec api python scripts/teardown_demo_data.py --yes

Teardown finds records by their prefixed titles, so an installation outside
demo mode also needs --allow-non-demo, as seeding it does.

This deliberately does not delete user accounts, frameworks, defined controls,
framework clauses or configuration such as the organisation's framework
selection. It targets records created by the seed script for the supplied
--prefix, plus relationships from those sample records and the automatic link
diary entries of the sample records, whose stored files are queued for deletion
from evidence storage. Change history stays, as it does whenever a record is
deleted.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
for candidate in (API_ROOT, Path.cwd()):
    if (candidate / "app" / "db" / "models.py").exists():
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))

from app.core.config import settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Artifact,
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    Base,
    BookStackSectionEvidence,
    Event,
    EventIncident,
    EventQuestionThread,
    InterestedParty,
    InterestedPartyCommunication,
    InterestedPartyControlLink,
    InterestedPartyName,
    InterestedPartyNature,
    IsmsAccessControlMatrixAwsAccount,
    IsmsAccessControlMatrixEntry,
    IsmsAccessControlMatrixRole,
    IsmsAwsAccount,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsDocumentFolder,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLicense,
    IsmsMeeting,
    IsmsMeetingAttendee,
    IsmsMeetingLink,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    IsmsPerson,
    IsmsSwot,
    IsmsSwotLink,
    IsmsVendor,
    Mapping,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    RiskInterestedParty,
    Story,
    StoryComment,
    StoryItem,
    StoryLink,
)
from app.db.session import SessionLocal  # noqa: E402
from app.security.diary_visibility import (  # noqa: E402
    DIARY_SOURCE,
    link_entry_external_id,
)
from app.services.evidence_retention import queue_object  # noqa: E402
from sqlalchemy import or_  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

RETENTION_HOLDS = Base.metadata.tables["audit_event_retention_holds"]
# Record types as the ISMS link tables name them, with the found collection
# that holds the seeded records of each type.
ISMS_LINKED_RECORDS = {
    "document": "documents",
    "objective": "objectives",
    "effectiveness_measure": "measures",
    "meeting": "meetings",
    "access_control_matrix": "access",
    "asset": "assets",
    "swot": "swot",
    "org_node": "org_nodes",
}
# Saving a record's framework links in the app maintains an automatic diary
# entry for it, so any seeded record of these types may have one.
LINK_ENTRY_RECORDS = {
    **ISMS_LINKED_RECORDS,
    "risk": "risks",
    "interested_party": "parties",
}


@dataclass(frozen=True)
class TeardownConfig:
    framework: str
    prefix: str
    dry_run: bool
    yes: bool


class DemoTeardown:
    def __init__(self, db: Session, config: TeardownConfig) -> None:
        self.db = db
        self.config = config
        self.stats: Counter[str] = Counter()

    @property
    def prefix(self) -> str:
        return self.config.prefix.strip() or "Demo"

    @property
    def framework(self) -> str:
        return self.config.framework.strip() or settings.default_framework_slug

    def account_code(self, suffix: str) -> str:
        base = re.sub(r"[^A-Z0-9]", "", self.prefix.upper()) or "DEMO"
        suffix = re.sub(r"[^A-Z0-9-]", "", suffix.upper()) or "GEN"
        return f"{base[:12]}-{suffix}"[:32]

    def starts_with(self, column: Any, text: str) -> Any:
        """``column`` starts with ``text``, read literally."""
        escaped = re.sub(r"([\\%_])", r"\\\1", text)
        return column.like(escaped + "%", escape="\\")

    def labelled(self, column: Any) -> Any:
        return self.starts_with(column, self.prefix + " ")

    def ids(self, query: Any) -> list[Any]:
        return [row[0] for row in query.all()]

    def labelled_ids(self, model: type, column: Any) -> list[Any]:
        return self.ids(self.db.query(model.id).filter(self.labelled(column)))

    def delete_where(self, model: Any, *criteria: Any) -> int:
        if not criteria:
            return 0
        table = getattr(model, "__table__", model)
        result = self.db.execute(table.delete().where(*criteria))
        count = int(result.rowcount or 0)
        if count:
            self.stats[table.name] += count
        return count

    def delete_ids(self, model: Any, column: Any, ids: Iterable[Any]) -> int:
        values = list(ids or [])
        if not values:
            return 0
        return self.delete_where(model, column.in_(values))

    def run(self) -> None:
        print(
            f"Removing Keen demo data for framework {self.framework!r} "
            f"with prefix {self.prefix!r}."
        )
        found = self.find()
        self.delete_stories(found)
        self.delete_audit_dependents(found)
        self.delete_event_dependents(found["events"])
        self.delete_isms_dependents(found)
        self.delete_ids(RiskControlLink, RiskControlLink.risk_id, found["risks"])
        self.delete_ids(
            RiskInterestedParty, RiskInterestedParty.risk_id, found["risks"]
        )
        self.delete_pestle_dependents(found)
        self.delete_interested_party_dependents(found["parties"])
        self.delete_parents(found)

    def find(self) -> dict[str, list[Any]]:
        db = self.db
        found = {
            "audits": self.labelled_ids(Audit, Audit.title),
            "documents": self.labelled_ids(IsmsDocument, IsmsDocument.title),
            "folders": self.labelled_ids(IsmsDocumentFolder, IsmsDocumentFolder.name),
            "objectives": self.labelled_ids(IsmsObjective, IsmsObjective.goal),
            "meetings": self.labelled_ids(IsmsMeeting, IsmsMeeting.title),
            "access": self.labelled_ids(
                IsmsAccessControlMatrixEntry, IsmsAccessControlMatrixEntry.task_action
            ),
            "org_nodes": self.labelled_ids(IsmsOrgNode, IsmsOrgNode.name),
            "licences": self.labelled_ids(IsmsLicense, IsmsLicense.name),
            "isms_processes": self.labelled_ids(
                IsmsBusinessProcess, IsmsBusinessProcess.name
            ),
            "pestle_processes": self.labelled_ids(
                PestleBusinessProcess, PestleBusinessProcess.name
            ),
            "pestle_items": self.labelled_ids(PestleItem, PestleItem.item),
            "party_names": self.labelled_ids(
                InterestedPartyName, InterestedPartyName.name
            ),
            "categories": self.labelled_ids(RiskCategory, RiskCategory.name),
            "people": self.labelled_ids(IsmsPerson, IsmsPerson.name),
            "vendors": self.labelled_ids(IsmsVendor, IsmsVendor.name),
            "swot": self.labelled_ids(IsmsSwot, IsmsSwot.title),
            "stories": self.labelled_ids(Story, Story.title),
        }
        found["measures"] = self.ids(
            db.query(IsmsEffectivenessMeasure.id).filter(
                or_(
                    self.labelled(IsmsEffectivenessMeasure.summary),
                    *(
                        self.starts_with(
                            IsmsEffectivenessMeasure.metric_key,
                            self.account_code(code) + "-",
                        )
                        for code in ("METRIC", "SLA")
                    ),
                )
            )
        )
        found["accounts"] = self.ids(
            db.query(IsmsAwsAccount.id).filter(
                or_(
                    self.labelled(IsmsAwsAccount.name),
                    self.starts_with(
                        IsmsAwsAccount.account_id,
                        (re.sub(r"[^A-Z0-9]", "", self.prefix.upper()) or "DEMO")[:12]
                        + "-",
                    ),
                )
            )
        )
        found["parties"] = (
            self.ids(
                db.query(InterestedParty.id).filter(
                    InterestedParty.name_id.in_(found["party_names"])
                )
            )
            if found["party_names"]
            else []
        )
        found["subcategories"] = self.ids(
            db.query(RiskAssetSubcategory.id).filter(
                or_(
                    self.labelled(RiskAssetSubcategory.name),
                    RiskAssetSubcategory.category_id.in_(found["categories"] or [None]),
                )
            )
        )
        found["assets"] = self.ids(
            db.query(RiskAsset.id).filter(
                or_(
                    self.labelled(RiskAsset.name),
                    RiskAsset.category_id.in_(found["categories"] or [None]),
                    RiskAsset.subcategory_id.in_(found["subcategories"] or [None]),
                    RiskAsset.license_id.in_(found["licences"] or [None]),
                )
            )
        )
        found["risks"] = (
            self.ids(db.query(Risk.id).filter(Risk.asset_id.in_(found["assets"])))
            if found["assets"]
            else []
        )
        link_entries = [
            link_entry_external_id(record_type, record_id)
            for record_type, key in LINK_ENTRY_RECORDS.items()
            for record_id in found[key]
        ]
        found["events"] = self.ids(
            db.query(Event.id).filter(
                or_(
                    # Every seeded collector event carries the prefix's code.
                    self.starts_with(
                        Event.external_id, self.account_code("EVENT") + "-"
                    ),
                    (Event.source == "demo-seed")
                    & (Event.external_id == self.account_code("SEED-ANCHOR")),
                    (Event.source == DIARY_SOURCE)
                    & self.starts_with(
                        Event.external_id, self.account_code("DIARY") + "-"
                    ),
                    (Event.source == DIARY_SOURCE)
                    & Event.external_id.in_(link_entries or [""]),
                    (Event.source == "demo")
                    & or_(
                        *(
                            self.starts_with(
                                Event.external_id, self.account_code(code) + "-"
                            )
                            for code in ("CATALOGUE", "SLA")
                        )
                    ),
                )
            )
        )
        return found

    def delete_stories(self, found: dict[str, list[Any]]) -> None:
        stories = found["stories"]
        self.delete_ids(StoryComment, StoryComment.story_id, stories)
        self.delete_ids(StoryLink, StoryLink.story_id, stories)
        self.delete_ids(StoryItem, StoryItem.story_id, stories)
        self.delete_ids(Story, Story.id, stories)

    def delete_audit_dependents(self, found: dict[str, list[Any]]) -> None:
        audits = found["audits"]
        for model in (
            AuditAttendee,
            AuditFinding,
            AuditScopedControl,
            AuditScopedClause,
            AuditScopedIsmsDocument,
            AuditSwotScope,
            AuditEvidence,
        ):
            self.delete_ids(model, model.audit_id, audits)
        self.delete_ids(
            AuditScopedIsmsDocument,
            AuditScopedIsmsDocument.document_id,
            found["documents"],
        )
        self.delete_ids(AuditSwotScope, AuditSwotScope.swot_id, found["swot"])
        self.delete_ids(AuditEvidence, AuditEvidence.event_id, found["events"])
        entity_targets = [
            ("isms_document", found["documents"]),
            ("isms_objective", found["objectives"]),
            ("isms_effectiveness_measure", found["measures"]),
            ("isms_meeting", found["meetings"]),
            ("isms_access_control_matrix", found["access"]),
            ("isms_asset", found["assets"]),
            ("isms_swot", found["swot"]),
            ("risk", found["risks"]),
            ("pestle_item", found["pestle_items"]),
            ("interested_party", found["parties"]),
            ("story", found["stories"]),
        ]
        for entity_type, ids in entity_targets:
            if ids:
                self.delete_where(
                    AuditEvidence,
                    AuditEvidence.entity_type == entity_type,
                    AuditEvidence.entity_id.in_(ids),
                )
        # Retention holds keep sampled events; they leave with their audits
        # and with the demo events themselves.
        self.delete_ids(RETENTION_HOLDS, RETENTION_HOLDS.c.audit_id, audits)
        self.delete_ids(RETENTION_HOLDS, RETENTION_HOLDS.c.event_id, found["events"])

    def delete_event_dependents(self, event_ids: list[Any]) -> None:
        self.delete_ids(Mapping, Mapping.event_id, event_ids)
        self.delete_ids(EventIncident, EventIncident.event_id, event_ids)
        self.delete_ids(EventQuestionThread, EventQuestionThread.event_id, event_ids)
        if event_ids:
            for (uri,) in self.db.query(Artifact.storage_uri).filter(
                Artifact.event_id.in_(event_ids)
            ):
                queue_object(self.db, uri)
        self.delete_ids(Artifact, Artifact.event_id, event_ids)

    def delete_isms_dependents(self, found: dict[str, list[Any]]) -> None:
        self.delete_ids(
            IsmsEffectivenessMetricEntry,
            IsmsEffectivenessMetricEntry.measure_id,
            found["measures"],
        )
        self.delete_ids(
            IsmsObjectiveResourceUser,
            IsmsObjectiveResourceUser.objective_id,
            found["objectives"],
        )
        self.delete_ids(IsmsMeetingLink, IsmsMeetingLink.meeting_id, found["meetings"])
        self.delete_ids(
            IsmsMeetingLink, IsmsMeetingLink.document_id, found["documents"]
        )
        self.delete_ids(
            IsmsMeetingAttendee, IsmsMeetingAttendee.meeting_id, found["meetings"]
        )
        self.delete_ids(
            IsmsAccessControlMatrixAwsAccount,
            IsmsAccessControlMatrixAwsAccount.entry_id,
            found["access"],
        )
        self.delete_ids(
            IsmsAccessControlMatrixAwsAccount,
            IsmsAccessControlMatrixAwsAccount.aws_account_id,
            found["accounts"],
        )
        self.delete_ids(
            IsmsAccessControlMatrixRole,
            IsmsAccessControlMatrixRole.entry_id,
            found["access"],
        )
        self.delete_ids(
            IsmsAccessControlMatrixRole,
            IsmsAccessControlMatrixRole.org_node_id,
            found["org_nodes"],
        )
        self.delete_ids(
            BookStackSectionEvidence,
            BookStackSectionEvidence.document_id,
            found["documents"],
        )
        for entity_type, key in ISMS_LINKED_RECORDS.items():
            ids = found[key]
            if not ids:
                continue
            for model in (IsmsEntityControlLink, IsmsEntityClauseLink):
                self.delete_where(
                    model, model.entity_type == entity_type, model.entity_id.in_(ids)
                )
        self.delete_ids(IsmsSwotLink, IsmsSwotLink.swot_id, found["swot"])
        if found["swot"]:
            # Child items refer to their parents; detach them before deleting.
            self.db.query(IsmsSwot).filter(IsmsSwot.id.in_(found["swot"])).update(
                {IsmsSwot.parent_id: None}, synchronize_session=False
            )

    def delete_pestle_dependents(self, found: dict[str, list[Any]]) -> None:
        self.delete_ids(
            PestleClauseRelevance,
            PestleClauseRelevance.pestle_item_id,
            found["pestle_items"],
        )
        self.delete_ids(
            PestleBusinessProcessRelevance,
            PestleBusinessProcessRelevance.pestle_item_id,
            found["pestle_items"],
        )
        self.delete_ids(
            PestleBusinessProcessRelevance,
            PestleBusinessProcessRelevance.business_process_id,
            found["pestle_processes"],
        )

    def delete_interested_party_dependents(self, party_ids: list[Any]) -> None:
        for model in (
            InterestedPartyControlLink,
            InterestedPartyCommunication,
            RiskInterestedParty,
        ):
            self.delete_ids(model, model.interested_party_id, party_ids)

    def delete_parents(self, found: dict[str, list[Any]]) -> None:
        self.delete_ids(Audit, Audit.id, found["audits"])
        self.delete_ids(Event, Event.id, found["events"])
        self.delete_ids(IsmsMeeting, IsmsMeeting.id, found["meetings"])
        self.delete_ids(IsmsObjective, IsmsObjective.id, found["objectives"])
        self.delete_ids(
            IsmsEffectivenessMeasure, IsmsEffectivenessMeasure.id, found["measures"]
        )
        self.delete_ids(
            IsmsAccessControlMatrixEntry,
            IsmsAccessControlMatrixEntry.id,
            found["access"],
        )
        self.delete_ids(IsmsAwsAccount, IsmsAwsAccount.id, found["accounts"])
        self.delete_ids(IsmsDocument, IsmsDocument.id, found["documents"])
        self.delete_ids(IsmsDocumentFolder, IsmsDocumentFolder.id, found["folders"])
        self.delete_ids(Risk, Risk.id, found["risks"])
        self.delete_ids(RiskAsset, RiskAsset.id, found["assets"])
        self.delete_ids(
            RiskAssetSubcategory, RiskAssetSubcategory.id, found["subcategories"]
        )
        self.delete_ids(RiskCategory, RiskCategory.id, found["categories"])
        self.delete_ids(IsmsLicense, IsmsLicense.id, found["licences"])
        self.delete_ids(PestleItem, PestleItem.id, found["pestle_items"])
        self.delete_ids(
            PestleBusinessProcess, PestleBusinessProcess.id, found["pestle_processes"]
        )
        self.delete_ids(InterestedParty, InterestedParty.id, found["parties"])
        self.delete_ids(
            InterestedPartyName, InterestedPartyName.id, found["party_names"]
        )
        self._delete_seeded_interested_party_natures_if_orphaned()
        self.delete_ids(
            IsmsBusinessProcess, IsmsBusinessProcess.id, found["isms_processes"]
        )
        self.delete_ids(IsmsSwot, IsmsSwot.id, found["swot"])
        self.delete_ids(IsmsPerson, IsmsPerson.id, found["people"])
        self.delete_ids(IsmsVendor, IsmsVendor.id, found["vendors"])
        self.delete_ids(IsmsOrgNode, IsmsOrgNode.id, found["org_nodes"])

    def _delete_seeded_interested_party_natures_if_orphaned(self) -> None:
        seeded_names = [
            "Expect secure and available service",
            "Need clear policies and secure tooling",
            "Expect notification and evidence records",
            "Supplier assurance and technical support",
        ]
        candidates = (
            self.db.query(InterestedPartyNature)
            .filter(InterestedPartyNature.name.in_(seeded_names))
            .all()
        )
        for nature in candidates:
            still_used = (
                self.db.query(InterestedParty.id)
                .filter(InterestedParty.nature_id == nature.id)
                .first()
            )
            if not still_used:
                self.db.delete(nature)
                self.stats[InterestedPartyNature.__tablename__] += 1
        self.db.flush()

    def print_summary(self) -> None:
        print("\nDeletion summary:")
        if not self.stats:
            print("  No matching demo data found.")
            return
        for key in sorted(self.stats):
            print(f"  {key}: {self.stats[key]}")


def parse_args() -> TeardownConfig:
    parser = argparse.ArgumentParser(
        description="Remove Keen demo data seeded by seed_demo_data.py."
    )
    parser.add_argument(
        "--framework",
        default="iso_27001_2022",
        help="Framework slug the seed data was written for; shown in the output.",
    )
    parser.add_argument(
        "--prefix",
        default="Demo",
        help="Seed prefix to remove. Must match the prefix used by seed_demo_data.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and delete inside a transaction, then roll back instead of committing.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required for a real teardown. Omit with --dry-run.",
    )
    parser.add_argument(
        "--allow-non-demo",
        action="store_true",
        help="Explicitly permit removing prefixed records from a non-demo installation.",
    )
    args = parser.parse_args()
    if not settings.demo_mode and not args.allow_non_demo:
        parser.error(
            "Use demo mode, or explicitly pass --allow-non-demo to remove demo records from this installation"
        )
    return TeardownConfig(
        framework=args.framework,
        prefix=args.prefix,
        dry_run=args.dry_run,
        yes=args.yes,
    )


def main() -> int:
    config = parse_args()
    if not config.dry_run and not config.yes:
        print(
            "Refusing to delete without --yes. Run with --dry-run first, then add --yes."
        )
        return 2
    db = SessionLocal()
    try:
        teardown = DemoTeardown(db, config)
        teardown.run()
        if config.dry_run:
            db.rollback()
            print("\nDry run complete; transaction rolled back.")
        else:
            db.commit()
            print("\nDemo data removed.")
        teardown.print_summary()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
