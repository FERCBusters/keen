"""Input rules shared by audit, finding, sample and schedule operations.

Evidence links stay on web schemes or same-origin paths, report text is
sanitised before storage, emails and schedule settings are checked, and
completed audits refuse changes until reopened.
"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.services.audits import validation as v
from app.services.errors import DomainError


def refused(call, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        call(*args, **kwargs)
    return error.value.status_code, error.value.detail


@pytest.mark.parametrize(
    "url",
    [
        "/event.html?id=7",
        "/risk.html?id=1#notes",
        "https://evidence.example/report.pdf",
        "HTTP://evidence.example/x",
    ],
)
def test_web_and_same_origin_evidence_links_are_kept(url):
    assert v.clean_url(f"  {url}  ") == url


@pytest.mark.parametrize(
    "url,detail",
    [
        ("x" * 2049, "evidence_url is too long"),
        ("/\\evil.example/x", "evidence_url must not contain backslashes"),
        ("/\tevil.example/x", "evidence_url must not contain backslashes"),
        ("https://evidence.example/a\nb", "evidence_url must not contain backslashes"),
        ("javascript:alert(1)", "Unsupported evidence_url scheme"),
        ("DATA:text/html,<b>x</b>", "Unsupported evidence_url scheme"),
        ("vbscript:msgbox(1)", "Unsupported evidence_url scheme"),
        ("//evil.example/x", "Protocol-relative evidence_url is not allowed"),
        ("ftp://files.example/report", "evidence_url must be http(s)"),
        ("https:///no-host", "evidence_url must be http(s)"),
        ("report.pdf", "evidence_url must be http(s)"),
        ("http://[::1/report", "Invalid evidence_url"),
    ],
)
def test_other_evidence_links_are_refused(url, detail):
    status, message = refused(v.clean_url, url)

    assert status == 400 and message.startswith(detail)


def test_blank_links_mean_no_link():
    assert v.clean_url("   ") is None and v.clean_url(None) is None


def test_event_ids_come_only_from_event_page_links():
    event = uuid.uuid4()

    assert v.event_id_from_url(f"/event.html?id={event}") == event
    assert v.event_id_from_url(f"https://keen.example/event.html?id={event}") == event
    for url in (
        f"/risk.html?id={event}",
        f"/isms-document.html?id={event}",
        "/event.html?id=not-a-uuid",
        "/event.html",
        f"http://[::1/event.html?id={event}",
        None,
    ):
        assert v.event_id_from_url(url) is None, url


@pytest.mark.parametrize(
    "email",
    ["@example.test", "auditor@", "no-at-sign", "a" * 250 + "@x.test", "a@b\x00c"],
)
def test_unusable_emails_are_refused(email):
    assert refused(v.clean_email, email, label="auditor email") == (
        400,
        "Invalid auditor email",
    )


def test_emails_are_trimmed_and_blank_means_none():
    assert v.clean_email("  lead@example.test ") == "lead@example.test"
    assert v.clean_email("  ") is None


@pytest.mark.parametrize(
    "user,expected",
    [
        (
            SimpleNamespace(email="lead@example.test", username="lead"),
            "lead@example.test",
        ),
        (
            SimpleNamespace(email="not-an-email", username="ops@example.test"),
            "ops@example.test",
        ),
        (SimpleNamespace(email=None, username="ops"), None),
        (None, None),
    ],
)
def test_the_email_snapshot_uses_the_best_address_a_user_has(user, expected):
    assert v.user_email_snapshot(user) == expected


@pytest.mark.parametrize(
    "call,value,detail",
    [
        (v.clean_schedule_interval, "weekly", "Invalid schedule_interval"),
        (v.clean_schedule_interval, 0, None),
        (v.clean_schedule_interval, 121, "schedule_interval must be between 1 and 120"),
        (v.clean_schedule_recurrence, "fortnightly", "Invalid schedule_recurrence"),
        (
            v.clean_schedule_date_rule,
            "last_friday_of_month",
            "Invalid schedule_date_rule",
        ),
        (v.clean_audit_type, "surveillance", "Invalid audit_type"),
    ],
)
def test_schedule_settings_outside_the_supported_set_are_refused(call, value, detail):
    if detail is None:
        # Zero reads as unset, which is one.
        assert call(value) == 1
        return
    assert refused(call, value) == (400, detail)


def test_anchor_months_are_required_only_for_first_weekday_rules():
    assert refused(v.clean_schedule_anchor_month, None, required=True) == (
        400,
        "schedule_anchor_month is required for first weekday schedules",
    )
    assert v.clean_schedule_anchor_month(" ", required=False) is None
    assert v.clean_schedule_anchor_month("3", required=True) == 3
    assert refused(v.clean_schedule_anchor_month, "March", required=True) == (
        400,
        "Invalid schedule_anchor_month",
    )
    assert refused(v.clean_schedule_anchor_month, 13, required=True) == (
        400,
        "schedule_anchor_month must be between 1 and 12",
    )


def test_first_weekday_rules_start_in_the_anchor_month():
    start = date(2026, 5, 20)

    assert v.schedule_start_for_rule(start, date_rule="exact", anchor_month=1) == start
    assert v.schedule_start_for_rule(
        start, date_rule="first_monday_of_month", anchor_month=6
    ) == date(2026, 6, 1)
    assert v.schedule_start_for_rule(
        start, date_rule="first_weekday_of_month", anchor_month=None
    ) == date(2026, 5, 1)


def test_framework_slugs_are_checked_and_default_to_the_deployment(monkeypatch):
    monkeypatch.setattr(settings, "default_framework_slug", "iso_27001_2022")

    assert v.clean_framework(None) == "iso_27001_2022"
    assert v.clean_framework(" KEEN-AF:1.0 ") == "KEEN-AF:1.0"
    for slug in ("iso 27001", "x" * 65, "fw;drop"):
        assert refused(v.clean_framework, slug) == (400, "Invalid framework_slug")
    monkeypatch.setattr(settings, "default_framework_slug", "")
    assert refused(v.clean_framework, "") == (400, "framework_slug is required")


def test_report_text_is_sanitised_and_bounded_after_sanitising():
    cleaned = v.clean_rich_text(
        '<p onclick="steal()">Summary</p><script>alert(1)</script>',
        max_len=200,
        label="executive_summary",
    )

    assert "Summary" in cleaned and "script" not in cleaned and "onclick" not in cleaned
    assert refused(v.clean_rich_text, "x" * 11, max_len=10, label="notes") == (
        400,
        "notes is too long",
    )
    assert refused(v.clean_text, " ", max_len=10, required=True, label="title") == (
        400,
        "title is required",
    )


@pytest.mark.parametrize("status", ["completed", "archived", " Completed "])
def test_completed_audits_refuse_changes_until_reopened(status):
    audit = SimpleNamespace(status=status)

    code, detail = refused(v.require_mutable_audit, audit, action="sampled")

    assert code == 409
    assert detail.startswith(
        f"This audit is {status.strip().lower()} and cannot be sampled."
    )


def test_open_audits_accept_changes_and_dates_must_run_forwards():
    v.require_mutable_audit(SimpleNamespace(status="in_progress"))
    v.require_mutable_audit(SimpleNamespace(status=None))
    v.validate_dates(date(2026, 1, 1), date(2026, 1, 1))
    assert refused(v.validate_dates, date(2026, 1, 2), date(2026, 1, 1)) == (
        400,
        "end_date cannot be before start_date",
    )
    assert refused(v.clean_status, "pending", v.FINDING_STATUSES, default="open") == (
        400,
        "Invalid status",
    )
    assert v.clean_status(None, v.FINDING_STATUSES, default="open") == "open"
