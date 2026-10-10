"""Compose ISMS feature routers in their original order.

Shared names are retained for existing imports; new code should import the
responsible submodule directly.
"""

from fastapi import APIRouter

from . import (
    access_control,
    assets,
    business_processes,
    documents,
    effectiveness,
    meetings,
    objectives,
    organisation,
    overview,
    personal,
    relationships,
)
from .access import (
    _can_read_isms as _can_read_isms,
)
from .access import (
    require_isms_manage as require_isms_manage,
)
from .access import (
    require_isms_read as require_isms_read,
)
from .common import (
    _clean_framework as _clean_framework,
)
from .serializers import (
    _app_config_out as _app_config_out,
)
from .serializers import (
    _asset_out as _asset_out,
)
from .serializers import (
    _business_process_out as _business_process_out,
)
from .serializers import (
    _document_out as _document_out,
)
from .serializers import (
    _effectiveness_measure_out as _effectiveness_measure_out,
)
from .serializers import (
    _entity_out as _entity_out,
)
from .serializers import (
    _meeting_out as _meeting_out,
)
from .serializers import (
    _objective_out as _objective_out,
)

router = APIRouter()
router.include_router(overview.router)
router.include_router(objectives.router)
router.include_router(documents.router)
router.include_router(organisation.router)
router.include_router(assets.router)
router.include_router(business_processes.router)
router.include_router(access_control.router)
router.include_router(effectiveness.router)
router.include_router(meetings.router)
router.include_router(relationships.router)
router.include_router(personal.router)
