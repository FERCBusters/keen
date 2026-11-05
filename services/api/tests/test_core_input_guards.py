"""Reminder lead-time settings and the mail library's reading of a mailbox.

Settings are built from synthetic environment values. The mail library is
replaced at its boundary to read an address differently from Keen's own
checks, as a changed parser could.
"""

import pytest
from app.core import email_address
from app.core.config import Settings
from pydantic import ValidationError


@pytest.mark.parametrize("days", ["-1", "91", "365"])
def test_a_reminder_lead_time_outside_ninety_days_is_refused(monkeypatch, days):
    monkeypatch.setenv("KEEN_REMINDER_DAYS_BEFORE", days)
    with pytest.raises(
        ValidationError,
        match="KEEN_REMINDER_DAYS_BEFORE must be an integer from 0 to 90",
    ):
        Settings()


@pytest.mark.parametrize("days,expected", [("0", 0), ("90", 90), (" 7 ", 7)])
def test_a_reminder_lead_time_within_ninety_days_is_kept(monkeypatch, days, expected):
    monkeypatch.setenv("KEEN_REMINDER_DAYS_BEFORE", days)
    assert Settings().reminder_days_before == expected


def test_a_reminder_lead_time_that_is_not_a_whole_number_is_refused(monkeypatch):
    monkeypatch.setenv("KEEN_REMINDER_DAYS_BEFORE", "soon")
    with pytest.raises(ValidationError, match="valid integer"):
        Settings()


@pytest.mark.parametrize(
    "reading",
    [
        [("", "auditor@example.test"), ("", "attacker@example.test")],
        [("Auditor", "auditor@example.test")],
        [("", "auditor@other.example.test")],
        [("", "")],
    ],
    ids=["two-addresses", "display-name", "other-address", "unparsed"],
)
def test_an_address_the_mail_library_reads_differently_is_refused(monkeypatch, reading):
    address = "auditor@example.test"
    # Keen's own checks accept the address, and today's parser agrees.
    assert email_address.single_mailbox(address) == address
    read = []

    def mail_library(values):
        read.append(values)
        return reading

    monkeypatch.setattr(email_address, "getaddresses", mail_library)

    assert email_address.single_mailbox(f" {address} ") is None
    assert read == [[address]]
