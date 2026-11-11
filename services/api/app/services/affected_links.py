"""Change who or what a risk or a PESTLE(E) factor affects.

Naming a record requires read access to the People, Vendors and asset
registers, and the record's feature to be on. A person or vendor can be added
by name from the picker only by someone who may add it in its own register.
Links the owner already holds stay as they are for anyone who may edit the
owner.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import AffectedLink, IsmsPerson, IsmsVendor, RiskAsset, User
from app.services import features
from app.services.affected import (
    KIND_FEATURES,
    KIND_LABELS,
    AffectedRef,
    asset_option,
    link_target,
    person_option,
    vendor_option,
)
from app.services.assurance import person_named, vendor_named
from app.services.errors import DomainError
from app.services.isms.access import can_manage_isms, can_read_isms
from app.services.text import clean_text

_OPTION_LIMIT = 500


def _require_feature(db: Session, kind: str) -> None:
    if not features.state(db).enabled(KIND_FEATURES[kind]):
        raise DomainError(
            400, f"{KIND_LABELS[kind]} records are disabled by an administrator"
        )


def replace_affected_links(
    db: Session,
    owner_links: list[AffectedLink],
    refs: list[AffectedRef],
    *,
    owner: dict[str, uuid.UUID],
    user: User,
) -> list[AffectedLink]:
    """Return the links ``refs`` describe, replacing ``owner_links``.

    ``owner`` names the link's owner column, such as ``{"risk_id": risk.id}``.
    The caller assigns the result to the owner's relationship and commits.
    """

    held = {link_target(link) for link in owner_links}
    readable: bool | None = None
    links: list[AffectedLink] = []
    seen: set[tuple[str, uuid.UUID | None]] = set()
    for ref in refs:
        ids = {"person": ref.person_id, "vendor": ref.vendor_id, "asset": ref.asset_id}
        names = {"person": ref.new_person, "vendor": ref.new_vendor}
        picked = [(kind, value) for kind, value in ids.items() if value is not None]
        named = [(kind, value) for kind, value in names.items() if value is not None]
        if len(picked) + len(named) != 1:
            raise DomainError(
                400,
                "Each affected record names one person, vendor or asset,"
                " or a new person or vendor by name",
            )
        target: tuple[str, uuid.UUID | None]
        if named:
            kind, name = named[0]
            _require_feature(db, kind)
            if not can_manage_isms(db, user):
                raise DomainError(
                    403,
                    f"Adding a {KIND_LABELS[kind].lower()} needs permission to"
                    " manage the ISMS registers",
                )
            row: IsmsPerson | IsmsVendor = (
                person_named(db, name) if kind == "person" else vendor_named(db, name)
            )
            target = (kind, row.id)
        else:
            target = picked[0]
            kind = target[0]
            if target not in held:
                _require_feature(db, kind)
                if readable is None:
                    readable = can_read_isms(db, user)
                if not readable:
                    raise DomainError(
                        403,
                        "Naming who is affected needs read access to People,"
                        " Vendors and Assets",
                    )
                model = {"person": IsmsPerson, "vendor": IsmsVendor}.get(
                    kind, RiskAsset
                )
                if db.get(model, target[1]) is None:
                    raise DomainError(400, f"Unknown {KIND_LABELS[kind].lower()}")
        if target in seen:
            continue
        seen.add(target)
        links.append(
            AffectedLink(
                **owner,
                **{f"{target[0]}_id": target[1]},
                created_at=utc_now_naive(),
            )
        )
    for link in owner_links:
        db.delete(link)
    db.flush()
    return links


def affected_options(*, q: str = "", user: User, db: Session) -> dict[str, Any]:
    """The people, vendors and assets a risk or PESTLE(E) factor can name.

    Each kind is listed while its feature is on. ``can_add`` says whether the
    caller may add a person or vendor by name.
    """

    needle = clean_text(q, max_len=256, label="q").lower()
    state = features.state(db)

    def listed(kind: str, model: Any, *columns: Any) -> list[Any]:
        if not state.enabled(KIND_FEATURES[kind]):
            return []
        query = db.query(model)
        if needle:
            like = f"%{needle}%"
            query = query.filter(
                func.lower(columns[0]).like(like) | func.lower(columns[1]).like(like)
            )
        return query.order_by(func.lower(model.name)).limit(_OPTION_LIMIT).all()

    can_add = can_manage_isms(db, user)
    return {
        "people": [
            person_option(row)
            for row in listed(
                "person", IsmsPerson, IsmsPerson.name, IsmsPerson.position
            )
        ],
        "vendors": [
            vendor_option(row)
            for row in listed(
                "vendor", IsmsVendor, IsmsVendor.name, IsmsVendor.description
            )
        ],
        "assets": [
            asset_option(row)
            for row in listed("asset", RiskAsset, RiskAsset.name, RiskAsset.description)
        ],
        "can_add": {
            "person": can_add and state.enabled("people"),
            "vendor": can_add and state.enabled("vendors"),
        },
    }
