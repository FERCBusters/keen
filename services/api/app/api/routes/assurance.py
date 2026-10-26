"""Organisation assurance registers. People do not require application accounts."""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.db.session import get_db
from app.services import assurance as service
from app.services.assurance import AssuranceInput, PersonInput, VendorInput

router = Router()


@router.get("/v1/isms/assurance/meta")
def assurance_meta(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return service.assurance_meta(user=user, db=db)


@router.get("/v1/isms/people")
def list_people(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return service.list_people(user=user, db=db)


@router.get("/v1/isms/people/{person_id}/workstation-posture")
def workstation_posture(
    person_id: uuid.UUID,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    from app.services.workstation_posture import person_posture

    return person_posture(db=db, user=user, person_id=person_id, offset=offset)


@router.post("/v1/isms/people", status_code=201)
def create_person(
    payload: PersonInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_person(payload=payload, user=user, db=db)


@router.patch("/v1/isms/people/{person_id}")
def update_person(
    person_id: uuid.UUID,
    payload: PersonInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_person(person_id=person_id, payload=payload, user=user, db=db)


@router.delete("/v1/isms/people/{person_id}", status_code=204)
def delete_person(
    person_id: uuid.UUID,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_person(person_id=person_id, user=user, db=db)


@router.get("/v1/isms/vendors")
def list_vendors(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return service.list_vendors(user=user, db=db)


@router.post("/v1/isms/vendors", status_code=201)
def create_vendor(
    payload: VendorInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_vendor(payload=payload, user=user, db=db)


@router.patch("/v1/isms/vendors/{vendor_id}")
def update_vendor(
    vendor_id: uuid.UUID,
    payload: VendorInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_vendor(vendor_id=vendor_id, payload=payload, user=user, db=db)


@router.delete("/v1/isms/vendors/{vendor_id}", status_code=204)
def delete_vendor(
    vendor_id: uuid.UUID,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_vendor(vendor_id=vendor_id, user=user, db=db)


@router.post("/v1/isms/people/{person_id}/assurances", status_code=201)
def add_assurance(
    person_id: uuid.UUID,
    payload: AssuranceInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.add_assurance(person_id=person_id, payload=payload, user=user, db=db)


@router.patch("/v1/isms/people/{person_id}/assurances/{assurance_id}")
def update_assurance(
    person_id: uuid.UUID,
    assurance_id: uuid.UUID,
    payload: AssuranceInput,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_assurance(
        person_id=person_id,
        assurance_id=assurance_id,
        payload=payload,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/people/{person_id}/assurances/{assurance_id}", status_code=204)
def delete_assurance(
    person_id: uuid.UUID,
    assurance_id: uuid.UUID,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_assurance(
        person_id=person_id, assurance_id=assurance_id, user=user, db=db
    )
