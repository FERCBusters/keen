"""Admin CRUD for built-in connections; secret values never appear in responses."""

from __future__ import annotations

from fastapi import Depends

from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import source_connections as service
from app.services.source_connections import ConnectionInput

from .integrations import integration_demo_guard

router = Router(
    prefix="/v1/admin/source-connections",
    dependencies=[Depends(require_admin), Depends(integration_demo_guard)],
)


@router.get("")
def listing(db=Depends(get_db)):
    # Listing must not decrypt stored credentials or resolve deployment secret files.
    return service.listing(db=db)


@router.post("")
def create(payload: ConnectionInput, db=Depends(get_db)):
    return service.create(payload=payload, db=db)


@router.put("/{key}")
def update(key: str, payload: ConnectionInput, db=Depends(get_db)):
    return service.update(key=key, payload=payload, db=db)


@router.delete("/{key}")
def delete(key: str, version: int, db=Depends(get_db)):
    return service.delete(key=key, version=version, db=db)


@router.post("/{key}/run")
def run(key: str, db=Depends(get_db)):
    return service.run(key=key, db=db)
