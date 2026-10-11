from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import interested_parties as service
from app.services.interested_parties import (
    CommunicationsPayload,
    ControlLinksPayload,
    InterestedPartyPayload,
    LookupPayload,
    _can_manage_interested_parties,
    _can_read_interested_parties,
)

router = Router()


def require_interested_parties_read(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = require_authenticated(request)
    if not _can_read_interested_parties(db, user):
        raise HTTPException(
            status_code=403, detail="interested_parties.read permission required"
        )
    return user


def require_interested_parties_manage(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = require_authenticated(request)
    if not _can_manage_interested_parties(db, user):
        raise HTTPException(
            status_code=403, detail="interested_parties.manage permission required"
        )
    return user


@router.get("/v1/interested-parties/meta")
def interested_parties_meta(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    user = require_authenticated(request)
    return service.interested_parties_meta(user=user, framework=framework, db=db)


@router.get("/v1/interested-parties/names")
def list_interested_party_names(
    q: str | None = None,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.list_interested_party_names(q=q, user=user, db=db)


@router.post("/v1/interested-parties/names")
def create_interested_party_name(
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.create_interested_party_name(payload=payload, user=user, db=db)


@router.patch("/v1/interested-parties/names/{name_id}")
def update_interested_party_name(
    name_id: str,
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.update_interested_party_name(
        name_id=name_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/interested-parties/names/{name_id}")
def delete_interested_party_name(
    name_id: str,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.delete_interested_party_name(name_id=name_id, user=user, db=db)


@router.get("/v1/interested-parties/natures")
def list_interested_party_natures(
    q: str | None = None,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.list_interested_party_natures(q=q, user=user, db=db)


@router.post("/v1/interested-parties/natures")
def create_interested_party_nature(
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.create_interested_party_nature(payload=payload, user=user, db=db)


@router.patch("/v1/interested-parties/natures/{nature_id}")
def update_interested_party_nature(
    nature_id: str,
    payload: LookupPayload,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.update_interested_party_nature(
        nature_id=nature_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/interested-parties/natures/{nature_id}")
def delete_interested_party_nature(
    nature_id: str,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.delete_interested_party_nature(nature_id=nature_id, user=user, db=db)


@router.get("/v1/interested-parties/visualisations")
def interested_parties_visualisations(
    framework: str = settings.default_framework_slug,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.interested_parties_visualisations(
        framework=framework, user=user, db=db
    )


@router.get("/v1/interested-parties")
def list_interested_parties(
    framework: str = settings.default_framework_slug,
    q: str | None = None,
    name_id: str | None = None,
    nature_id: str | None = None,
    control: str | None = None,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.list_interested_parties(
        framework=framework,
        q=q,
        name_id=name_id,
        nature_id=nature_id,
        control=control,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.post("/v1/interested-parties")
def create_interested_party(
    payload: InterestedPartyPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.create_interested_party(
        payload=payload, context=change_context(request), user=user, db=db
    )


@router.get("/v1/interested-parties/{party_id}")
def get_interested_party(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.get_interested_party(party_id=party_id, user=user, db=db)


@router.patch("/v1/interested-parties/{party_id}")
def update_interested_party(
    party_id: str,
    payload: InterestedPartyPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.update_interested_party(
        party_id=party_id,
        payload=payload,
        context=change_context(request),
        user=user,
        db=db,
    )


@router.delete("/v1/interested-parties/{party_id}")
def delete_interested_party(
    party_id: str,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.delete_interested_party(
        party_id=party_id, context=change_context(request), user=user, db=db
    )


@router.get("/v1/interested-parties/{party_id}/controls")
def interested_party_controls(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.interested_party_controls(party_id=party_id, user=user, db=db)


@router.patch("/v1/interested-parties/{party_id}/controls")
def update_interested_party_controls(
    party_id: str,
    payload: ControlLinksPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.update_interested_party_controls(
        party_id=party_id,
        payload=payload,
        context=change_context(request),
        user=user,
        db=db,
    )


@router.get("/v1/interested-parties/{party_id}/communications")
def interested_party_communications(
    party_id: str,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.interested_party_communications(party_id=party_id, user=user, db=db)


@router.patch("/v1/interested-parties/{party_id}/communications")
def update_interested_party_communications(
    party_id: str,
    payload: CommunicationsPayload,
    request: Request,
    user=Depends(require_interested_parties_manage),
    db: Session = Depends(get_db),
):
    return service.update_interested_party_communications(
        party_id=party_id,
        payload=payload,
        context=change_context(request),
        user=user,
        db=db,
    )


@router.get("/v1/interested-parties/{party_id}/changelog")
def interested_party_changelog(
    party_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.interested_party_changelog(
        party_id=party_id, limit=limit, offset=offset, user=user, db=db
    )


@router.get("/v1/controls/{control_id}/interested-parties")
def control_interested_parties(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_interested_parties_read),
    db: Session = Depends(get_db),
):
    return service.control_interested_parties(
        control_id=control_id, framework=framework, user=user, db=db
    )
