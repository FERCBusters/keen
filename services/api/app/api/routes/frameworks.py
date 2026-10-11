from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.session import get_db
from app.schemas import FrameworkUpsert
from app.security.auth import require_admin
from app.services import frameworks as service
from app.services.frameworks import FrameworkNodeInput, FrameworkSelectionInput

router = Router()


@router.get("/v1/frameworks")
def list_frameworks(db: Session = Depends(get_db)):
    return service.list_frameworks(db=db)


@router.get("/v1/admin/framework-selection", dependencies=[Depends(require_admin)])
def framework_selection(db: Session = Depends(get_db)):
    return service.framework_selection(db=db)


@router.put("/v1/admin/framework-selection")
def save_framework_selection(
    payload: FrameworkSelectionInput,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.save_framework_selection(payload=payload, user=user, db=db)


@router.post("/v1/frameworks", dependencies=[Depends(require_admin)])
def upsert_framework(payload: FrameworkUpsert, db: Session = Depends(get_db)):
    return service.upsert_framework(payload=payload, db=db)


@router.get("/v1/admin/frameworks/{slug}/nodes", dependencies=[Depends(require_admin)])
def framework_nodes(slug: str, db: Session = Depends(get_db)):
    return service.framework_nodes(slug=slug, db=db)


@router.put(
    "/v1/admin/frameworks/{slug}/nodes/{kind}/{ref:path}",
    dependencies=[Depends(require_admin)],
)
def save_framework_node(
    slug: str,
    kind: str,
    ref: str,
    payload: FrameworkNodeInput,
    db: Session = Depends(get_db),
):
    return service.save_framework_node(
        slug=slug, kind=kind, ref=ref, payload=payload, db=db
    )


@router.delete(
    "/v1/admin/frameworks/{slug}/nodes/{kind}/{ref:path}",
    dependencies=[Depends(require_admin)],
)
def delete_framework_node(
    slug: str, kind: str, ref: str, db: Session = Depends(get_db)
):
    return service.delete_framework_node(slug=slug, kind=kind, ref=ref, db=db)
