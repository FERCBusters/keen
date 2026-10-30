"""Universal Evidence Utility catalogues, for any signed-in account."""

from __future__ import annotations

from fastapi import Depends, Response

from app.api.routing import Router
from app.security.auth import require_authenticated
from app.services import ueu_catalogues as service
from app.storage.filenames import content_disposition_attachment

router = Router()


@router.get("/v1/ueu/catalogues")
def list_catalogues(user=Depends(require_authenticated)):
    return service.list_catalogues()


@router.get("/v1/ueu/catalogues/{catalogue_id}/{version}")
def get_catalogue(catalogue_id: str, version: str, user=Depends(require_authenticated)):
    data, filename = service.catalogue_file(catalogue_id, version)
    return Response(
        data,
        media_type="application/json",
        headers={"Content-Disposition": content_disposition_attachment(filename)},
    )
