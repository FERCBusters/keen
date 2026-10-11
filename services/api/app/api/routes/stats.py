from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services import stats as service

router = Router()


@router.get("/v1/stats/summary")
def stats_summary(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    return service.stats_summary(
        context=change_context(request), framework=framework, db=db
    )


@router.get("/v1/stats/controls")
def stats_controls(
    request: Request,
    framework: str = settings.default_framework_slug,
    limit: int = 2000,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    return service.stats_controls(
        context=change_context(request),
        framework=framework,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )


@router.get("/v1/charts/event_volume_timeseries")
def stats_events_timeseries(
    request: Request,
    days: int = 120,
    framework: str = settings.default_framework_slug,
    source: Optional[str] = None,
    breakdown: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    # Optional timestamp window (UTC, end exclusive). When provided, it takes
    # precedence over start_date/end_date/days.
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    # Bucket size ("day"|"hour"|"minute"|"second").
    interval: str = "day",
    db: Session = Depends(get_db),
):
    "Return event volume over time.\n\n    The histogram is bucketed by `interval` (default: daily). The endpoint\n    supports either a date window (start_date/end_date inclusive) or a\n    timestamp window (start_ts/end_ts, end exclusive).\n\n    Counts:\n    - total: total events\n    - mapped: distinct events mapped to the requested framework\n    - unmapped: total - mapped\n\n    This endpoint is used by the UI visualisations (histograms).\n"
    return service.stats_events_timeseries(
        context=change_context(request),
        days=days,
        framework=framework,
        source=source,
        breakdown=breakdown,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        interval=interval,
        db=db,
    )
