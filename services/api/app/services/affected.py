"""Who or what a risk or a PESTLE(E) factor affects: the shared link shape.

A risk and a PESTLE(E) item name the people, vendors (third parties) and
assets they affect through one kind of link (``AffectedLink``), so every page,
picker and suggestion reads them the same way. A risk's own asset, "What is
affected", stays on the risk; these links add the people, third parties and
other assets it reaches.

This module describes links; app.services.affected_links changes them and
lists the records a picker offers.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from app.db.models import AffectedLink, IsmsPerson, IsmsVendor, RiskAsset

MAX_AFFECTED = 100
KIND_LABELS = {"person": "Person", "vendor": "Vendor / 3rd party", "asset": "Asset"}
# The feature that holds each kind of record.
KIND_FEATURES = {"person": "people", "vendor": "vendors", "asset": "assets"}
_PAGES = {
    "person": "/person.html?id={id}",
    "vendor": "/vendor.html?id={id}",
    "asset": "/isms-asset.html?id={id}",
}


class AffectedRef(BaseModel):
    """One record a risk or PESTLE(E) factor affects.

    Name an existing person, vendor or asset by its ID, or a new person or
    vendor by name; a name already recorded is reused.
    """

    person_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    new_person: str | None = Field(default=None, max_length=256)
    new_vendor: str | None = Field(default=None, max_length=256)


def affected_loads(collection: Any) -> LoaderOption:
    """Load an owner's affected links, and the records they name, with a page."""

    return selectinload(collection).options(
        selectinload(AffectedLink.person),
        selectinload(AffectedLink.vendor),
        selectinload(AffectedLink.asset).options(
            selectinload(RiskAsset.category), selectinload(RiskAsset.subcategory)
        ),
    )


def link_target(link: AffectedLink) -> tuple[str, uuid.UUID | None]:
    if link.person_id:
        return "person", link.person_id
    if link.vendor_id:
        return "vendor", link.vendor_id
    # A link names exactly one target (ck_affected_links_one_target).
    return "asset", link.asset_id


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def person_option(row: IsmsPerson) -> dict[str, Any]:
    return {
        "kind": "person",
        "id": str(row.id),
        "label": row.name,
        "detail": row.position,
    }


def vendor_option(row: IsmsVendor) -> dict[str, Any]:
    return {
        "kind": "vendor",
        "id": str(row.id),
        "label": row.name,
        "detail": _short(row.description) or row.website,
    }


def asset_option(row: RiskAsset) -> dict[str, Any]:
    return {
        "kind": "asset",
        "id": str(row.id),
        "label": row.name,
        "detail": f"{row.category.name} › {row.subcategory.name}",
    }


def record_out(row: IsmsPerson | IsmsVendor | RiskAsset) -> dict[str, Any]:
    """Describe a person, vendor or asset as an affected record."""

    if isinstance(row, IsmsPerson):
        out = person_option(row)
    elif isinstance(row, IsmsVendor):
        out = vendor_option(row)
    else:
        out = asset_option(row)
    out["kind_label"] = KIND_LABELS[out["kind"]]
    out["href"] = _PAGES[out["kind"]].format(id=row.id)
    return out


def affected_out(link: AffectedLink) -> dict[str, Any] | None:
    """Describe one affected record; None once the record is gone.

    A record removed by another request after its link was read is gone,
    though the database removes the link with it.
    """

    kind, _target_id = link_target(link)
    row: IsmsPerson | IsmsVendor | RiskAsset | None
    if kind == "person":
        row = link.person
    elif kind == "vendor":
        row = link.vendor
    else:
        row = link.asset
    return None if row is None else record_out(row)


def affected_list(links: list[AffectedLink]) -> list[dict[str, Any]]:
    return [item for link in links if (item := affected_out(link)) is not None]


def affected_labels(links: list[AffectedLink]) -> list[str]:
    """Each affected record as "Name (kind)", for change history."""

    return [
        f"{item['label']} ({KIND_LABELS[item['kind']].lower()})"
        for item in affected_list(links)
    ]
