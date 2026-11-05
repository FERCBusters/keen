"""The date that decides which months are closed for automatic zero entries.

A configured time zone that cannot be read at all, as opposed to one that is
merely unknown, falls back to the server's date and is logged with its cause.
"""

import logging
from datetime import date

import pytest
from app.core.config import settings
from app.services import effectiveness_metrics as metrics


@pytest.mark.parametrize("zone", ["../Etc/UTC", "/etc/localtime", "UTC\x00"])
def test_an_unreadable_time_zone_falls_back_to_the_server_date(
    monkeypatch, caplog, zone
):
    monkeypatch.setattr(settings, "timezone", zone)

    with caplog.at_level(logging.ERROR, logger=metrics.log.name):
        today = metrics._today_for_effectiveness_metrics()

    assert today == date.today()
    (entry,) = caplog.records
    assert entry.getMessage().startswith(
        f"Failed to resolve KEEN_TIMEZONE={zone!r}; falling back to server-local date"
    )
    assert entry.exc_info[0] is ValueError
