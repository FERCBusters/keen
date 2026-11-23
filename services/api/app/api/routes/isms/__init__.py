"""Compose ISMS HTTP adapters in routing order."""

from mosp.api.routing import Router

from . import (
    access_control,
    assets,
    documents,
    effectiveness,
    meetings,
    objectives,
    organisation,
    overview,
    personal,
    relationships,
    swot,
)

router = Router()
router.include_router(overview.router)
router.include_router(objectives.router)
router.include_router(swot.router)
router.include_router(documents.router)
router.include_router(organisation.router)
router.include_router(assets.router)
router.include_router(access_control.router)
router.include_router(effectiveness.router)
router.include_router(meetings.router)
router.include_router(relationships.router)
router.include_router(personal.router)
