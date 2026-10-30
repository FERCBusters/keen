"""The UEU event catalogues KEEN holds, published for documentation and reuse.

Catalogues are vocabularies, not tenant data: each names event types and the
typed fields they may carry. KEEN lists every catalogue it loaded, built-in or
from ``KEEN_UEU_CATALOGUE_DIR``, and serves each file exactly as it was read.
"""

from __future__ import annotations

import hashlib

from app.agents.ueu import catalogue as catalogues
from app.services.errors import DomainError


def list_catalogues() -> dict:
    items = []
    for (ident, version), held in sorted(catalogues.catalogues().items()):
        items.append(
            {
                "id": ident,
                "version": version,
                "description": held.description,
                "origin": held.origin,
                "event_types": len(held.event_types),
                "sha256": hashlib.sha256(held.content).hexdigest(),
                "size_bytes": len(held.content),
            }
        )
    return {"items": items}


def catalogue_file(ident: str, version: str) -> tuple[bytes, str]:
    """The catalogue's file as loaded, and a download name for it."""
    held = catalogues.lookup(ident, version)
    if held is None:
        raise DomainError(404, "Catalogue not found")
    return held.content, f"{held.id}-{held.version}.json"
