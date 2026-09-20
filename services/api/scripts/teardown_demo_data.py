#!/usr/bin/env python3
"""Remove KEEN demonstration data created by seed_demo_data.py.

Intended location:
    services/api/scripts/teardown_demo_data.py

Typical use:
    docker compose exec api python scripts/teardown_demo_data.py --dry-run
    docker compose exec api python scripts/teardown_demo_data.py --yes

This deliberately does not delete user accounts, frameworks, defined controls, or
framework clauses. It targets records created by the seed script for the supplied
--prefix, plus relationships from those sample records.
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
)
from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import or_  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


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

    @property
    def like_prefix(self) -> str:
        return f"{self.prefix} %"

    def account_code(self, suffix: str) -> str:
        base = re.sub(r"[^A-Z0-9]", "", self.prefix.upper()) or "DEMO"
        suffix = re.sub(r"[^A-Z0-9-]", "", suffix.upper()) or "GEN"
        return f"{base[:12]}-{suffix}"[:32]

    def ids(self, query: Any) -> list[Any]:
        return [row[0] for row in query.all()]

    def delete_where(self, model: type, *criteria: Any) -> int:
        if not criteria:
            return 0
        count = self.db.query(model).filter(*criteria).delete(synchronize_session=False)
        if count:
            self.stats[getattr(model, "__tablename__", model.__name__)] += count
        return int(count or 0)

    def delete_ids(self, model: type, column: Any, ids: Iterable[Any]) -> int:
        values = list(ids or [])
        if not values:
            return 0
        return self.delete_where(model, column.in_(values))

    def prefixed_ids(self, model: type, column: Any) -> list[Any]:
        return self.ids(self.db.query(model.id).filter(column.like(self.like_prefix)))

    def run(self) -> None:
        print(
            f"Removing KEEN demo data for framework {self.framework!r} "
            f"with prefix {self.prefix!r}."
        )

        audit_ids = self.prefixed_ids(Audit, Audit.title)
        document_ids = self.prefixed_ids(IsmsDocument, IsmsDocument.title)
        objective_ids = self.prefixed_ids(IsmsObjective, IsmsObjective.goal)
        meeting_ids = self.prefixed_ids(IsmsMeeting, IsmsMeeting.title)
        measure_ids = self.ids(
            self.db.query(IsmsEffectivenessMeasure.id).filter(
                or_(
                    IsmsEffectivenessMeasure.summary.like(self.like_prefix),
                    IsmsEffectivenessMeasure.metric_key.like(
                        f"{self.account_code('METRIC')}-%"
                    ),
                )
            )
        )
        access_entry_ids = self.prefixed_ids(
            IsmsAccessControlMatrixEntry, IsmsAccessControlMatrixEntry.task_action
        )
        account_ids = self.ids(
            self.db.query(IsmsAwsAccount.id).filter(
                or_(
                    IsmsAwsAccount.name.like(self.like_prefix),
                    IsmsAwsAccount.account_id.like(
                        f"{(re.sub(r'[^A-Z0-9]', '', self.prefix.upper()) or 'DEMO')[:12]}-%"
                    ),
                )
            )
        )
        org_node_ids = self.prefixed_ids(IsmsOrgNode, IsmsOrgNode.name)
        license_ids = self.prefixed_ids(IsmsLicense, IsmsLicense.name)
        isms_process_ids = self.prefixed_ids(
            IsmsBusinessProcess, IsmsBusinessProcess.name
        )
        pestle_process_ids = self.prefixed_ids(
            PestleBusinessProcess, PestleBusinessProcess.name
        )
        pestle_item_ids = self.prefixed_ids(PestleItem, PestleItem.item)
        interested_party_name_ids = self.prefixed_ids(
            InterestedPartyName, InterestedPartyName.name
        )
        interested_party_ids = (
            self.ids(
                self.db.query(InterestedParty.id).filter(
                    InterestedParty.name_id.in_(interested_party_name_ids),
                    InterestedParty.framework_slug == self.framework,
                )
            )
            if interested_party_name_ids
            else []
        )
        category_ids = self.prefixed_ids(RiskCategory, RiskCategory.name)
        subcategory_ids = self.ids(
            self.db.query(RiskAssetSubcategory.id).filter(
                or_(
                    RiskAssetSubcategory.name.like(self.like_prefix),
                    RiskAssetSubcategory.category_id.in_(category_ids or [None]),
                )
            )
        )
        asset_ids = self.ids(
            self.db.query(RiskAsset.id).filter(
                or_(
                    RiskAsset.name.like(self.like_prefix),
                    RiskAsset.category_id.in_(category_ids or [None]),
                    RiskAsset.subcategory_id.in_(subcategory_ids or [None]),
                    RiskAsset.license_id.in_(license_ids or [None]),
                )
            )
        )
        risk_ids = (
            self.ids(self.db.query(Risk.id).filter(Risk.asset_id.in_(asset_ids)))
            if asset_ids
            else []
        )
        diary_event_ids = self.ids(
            self.db.query(Event.id).filter(
                Event.source == "diary",
                or_(
                    Event.external_id.like(f"{self.account_code('DIARY')}-%"),
                    Event.summary.like(f"diary: {self.like_prefix}"),
                ),
            )
        )
        source_event_ids = self.ids(
            self.db.query(Event.id).filter(
                Event.source.in_(
                    [
                        "loki",
                        "jenkins",
                        "github",
                        "forgejo",
                        "webhook:enroll",
                    ]
                ),
                or_(
                    Event.external_id.like(f"{self.account_code('EVENT')}-%"),
                    Event.summary.like(self.like_prefix),
                ),
            )
        )
        event_ids = diary_event_ids + source_event_ids

        self._delete_audit_dependents(
            audit_ids,
            document_ids,
            event_ids,
            objective_ids,
            measure_ids,
            meeting_ids,
            access_entry_ids,
            asset_ids,
            risk_ids,
            pestle_item_ids,
            interested_party_ids,
        )
        self._delete_event_dependents(event_ids)
        self._delete_isms_dependents(
            document_ids,
            objective_ids,
            measure_ids,
            meeting_ids,
            access_entry_ids,
            account_ids,
            org_node_ids,
            asset_ids,
        )
        self._delete_risk_dependents(risk_ids)
        self._delete_pestle_dependents(pestle_item_ids, pestle_process_ids)
        self._delete_interested_party_dependents(interested_party_ids)

        # Parent rows, after relationships have been removed.
        self.delete_ids(Audit, Audit.id, audit_ids)
        self.delete_ids(Event, Event.id, event_ids)
        self.delete_ids(IsmsMeeting, IsmsMeeting.id, meeting_ids)
        self.delete_ids(IsmsObjective, IsmsObjective.id, objective_ids)
        self.delete_ids(
            IsmsEffectivenessMeasure, IsmsEffectivenessMeasure.id, measure_ids
        )
        self.delete_ids(
            IsmsAccessControlMatrixEntry,
            IsmsAccessControlMatrixEntry.id,
            access_entry_ids,
        )
        self.delete_ids(IsmsAwsAccount, IsmsAwsAccount.id, account_ids)
        self.delete_ids(IsmsDocument, IsmsDocument.id, document_ids)
        self.delete_ids(Risk, Risk.id, risk_ids)
        self.delete_ids(RiskAsset, RiskAsset.id, asset_ids)
        self.delete_ids(RiskAssetSubcategory, RiskAssetSubcategory.id, subcategory_ids)
        self.delete_ids(RiskCategory, RiskCategory.id, category_ids)
        self.delete_ids(IsmsLicense, IsmsLicense.id, license_ids)
        self.delete_ids(PestleItem, PestleItem.id, pestle_item_ids)
        self.delete_ids(
            PestleBusinessProcess, PestleBusinessProcess.id, pestle_process_ids
        )
        self.delete_ids(InterestedParty, InterestedParty.id, interested_party_ids)
        self.delete_ids(
            InterestedPartyName, InterestedPartyName.id, interested_party_name_ids
        )
        self._delete_seeded_interested_party_natures_if_orphaned()
        self.delete_ids(IsmsBusinessProcess, IsmsBusinessProcess.id, isms_process_ids)
        self.delete_ids(IsmsOrgNode, IsmsOrgNode.id, org_node_ids)

    def _delete_audit_dependents(
        self,
        audit_ids: list[Any],
        document_ids: list[Any],
        event_ids: list[Any],
        objective_ids: list[Any],
        measure_ids: list[Any],
        meeting_ids: list[Any],
        access_entry_ids: list[Any],
        asset_ids: list[Any],
        risk_ids: list[Any],
        pestle_item_ids: list[Any],
        interested_party_ids: list[Any],
    ) -> None:
        self.delete_ids(AuditAttendee, AuditAttendee.audit_id, audit_ids)
        self.delete_ids(AuditFinding, AuditFinding.audit_id, audit_ids)
        self.delete_ids(AuditScopedControl, AuditScopedControl.audit_id, audit_ids)
        self.delete_ids(AuditScopedClause, AuditScopedClause.audit_id, audit_ids)
        self.delete_ids(
            AuditScopedIsmsDocument, AuditScopedIsmsDocument.audit_id, audit_ids
        )
        self.delete_ids(
            AuditScopedIsmsDocument, AuditScopedIsmsDocument.document_id, document_ids
        )
        self.delete_ids(AuditEvidence, AuditEvidence.audit_id, audit_ids)
        self.delete_ids(AuditEvidence, AuditEvidence.event_id, event_ids)
        entity_targets = [
            ("isms_document", document_ids),
            ("isms_objective", objective_ids),
            ("isms_effectiveness_measure", measure_ids),
            ("isms_meeting", meeting_ids),
            ("isms_access_control_matrix", access_entry_ids),
            ("isms_asset", asset_ids),
            ("risk", risk_ids),
            ("pestle_item", pestle_item_ids),
            ("interested_party", interested_party_ids),
        ]
        for entity_type, ids in entity_targets:
            if ids:
                self.delete_where(
                    AuditEvidence,
                    AuditEvidence.entity_type == entity_type,
                    AuditEvidence.entity_id.in_(ids),
                )

    def _delete_event_dependents(self, event_ids: list[Any]) -> None:
        self.delete_ids(Mapping, Mapping.event_id, event_ids)
        self.delete_ids(EventIncident, EventIncident.event_id, event_ids)
        self.delete_ids(Artifact, Artifact.event_id, event_ids)
        self.delete_ids(EventQuestionThread, EventQuestionThread.event_id, event_ids)

    def _delete_isms_dependents(
        self,
        document_ids: list[Any],
        objective_ids: list[Any],
        measure_ids: list[Any],
        meeting_ids: list[Any],
        access_entry_ids: list[Any],
        account_ids: list[Any],
        org_node_ids: list[Any],
        asset_ids: list[Any],
    ) -> None:
        self.delete_ids(
            IsmsEffectivenessMetricEntry,
            IsmsEffectivenessMetricEntry.measure_id,
            measure_ids,
        )
        self.delete_ids(
            IsmsObjectiveResourceUser,
            IsmsObjectiveResourceUser.objective_id,
            objective_ids,
        )
        self.delete_ids(IsmsMeetingLink, IsmsMeetingLink.meeting_id, meeting_ids)
        self.delete_ids(IsmsMeetingLink, IsmsMeetingLink.document_id, document_ids)
        self.delete_ids(
            IsmsMeetingAttendee, IsmsMeetingAttendee.meeting_id, meeting_ids
        )
        self.delete_ids(
            IsmsAccessControlMatrixAwsAccount,
            IsmsAccessControlMatrixAwsAccount.entry_id,
            access_entry_ids,
        )
        self.delete_ids(
            IsmsAccessControlMatrixAwsAccount,
            IsmsAccessControlMatrixAwsAccount.aws_account_id,
            account_ids,
        )
        self.delete_ids(
            IsmsAccessControlMatrixRole,
            IsmsAccessControlMatrixRole.entry_id,
            access_entry_ids,
        )
        self.delete_ids(
            IsmsAccessControlMatrixRole,
            IsmsAccessControlMatrixRole.org_node_id,
            org_node_ids,
        )
        entity_targets = [
            ("document", document_ids),
            ("objective", objective_ids),
            ("effectiveness_measure", measure_ids),
            ("meeting", meeting_ids),
            ("access_control_matrix", access_entry_ids),
            ("asset", asset_ids),
        ]
        for entity_type, ids in entity_targets:
            if ids:
                self.delete_where(
                    IsmsEntityControlLink,
                    IsmsEntityControlLink.entity_type == entity_type,
                    IsmsEntityControlLink.entity_id.in_(ids),
                )
                self.delete_where(
                    IsmsEntityClauseLink,
                    IsmsEntityClauseLink.entity_type == entity_type,
                    IsmsEntityClauseLink.entity_id.in_(ids),
                )

    def _delete_risk_dependents(self, risk_ids: list[Any]) -> None:
        self.delete_ids(RiskControlLink, RiskControlLink.risk_id, risk_ids)

    def _delete_pestle_dependents(
        self, pestle_item_ids: list[Any], pestle_process_ids: list[Any]
    ) -> None:
        self.delete_ids(
            PestleClauseRelevance, PestleClauseRelevance.pestle_item_id, pestle_item_ids
        )
        self.delete_ids(
            PestleBusinessProcessRelevance,
            PestleBusinessProcessRelevance.pestle_item_id,
            pestle_item_ids,
        )
        self.delete_ids(
            PestleBusinessProcessRelevance,
            PestleBusinessProcessRelevance.business_process_id,
            pestle_process_ids,
        )

    def _delete_interested_party_dependents(
        self, interested_party_ids: list[Any]
    ) -> None:
        self.delete_ids(
            InterestedPartyControlLink,
            InterestedPartyControlLink.interested_party_id,
            interested_party_ids,
        )
        self.delete_ids(
            InterestedPartyCommunication,
            InterestedPartyCommunication.interested_party_id,
            interested_party_ids,
        )

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

    def print_summary(self) -> None:
        print("\nDeletion summary:")
        if not self.stats:
            print("  No matching demo data found.")
            return
        for key in sorted(self.stats):
            print(f"  {key}: {self.stats[key]}")


def parse_args() -> TeardownConfig:
    parser = argparse.ArgumentParser(
        description="Remove KEEN demo data seeded by seed_demo_data.py."
    )
    parser.add_argument(
        "--framework",
        default=settings.default_framework_slug,
        help="Framework slug used by the seed data. Defaults to KEEN settings.default_framework_slug.",
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
    args = parser.parse_args()
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
