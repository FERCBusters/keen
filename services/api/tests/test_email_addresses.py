"""Every email address Keen stores or sends to is exactly one mailbox.

A recipient reaches an SMTP To header, where a comma or semicolon starts another
address, and smtplib sends to every address the header lists. One helper
(app.core.email_address.single_mailbox) decides what an address is; request
values pass through app.services.text.clean_email, identity-provider and
directory addresses through the helper itself, and the mailer checks again and
names its one envelope recipient. The sweep below refuses a hand-rolled email
check anywhere else, so a new field cannot accept a list of addresses.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.email_address import single_mailbox
from app.services import mailer, security_notifications
from app.services.errors import DomainError
from app.services.text import clean_email

APP = Path(__file__).resolve().parents[1] / "app"
HELPER = APP / "core" / "email_address.py"
# "@" tests outside the helper that are not about email addresses.
NOT_EMAIL = {
    # URL userinfo before the host, removed from displayed URLs.
    ("security/redaction.py", "netloc"),
}

LISTS = [
    "victim@example.com, attacker@example.com",
    "victim@example.com;attacker@example.com",
    "Victim <victim@example.com>",
    "victim@example.com (attacker@example.com)",
    '"victim@example.com" <attacker@example.com>',
    "victim@example.com\r\nBcc: attacker@example.com",
    "victim@example.com attacker@example.com",
]
MALFORMED = [
    "@example.test",
    "auditor@",
    "no-at-sign",
    "a" * 250 + "@x.test",
    "a@b\x00c",
]


@pytest.mark.parametrize("value", LISTS + MALFORMED)
def test_lists_and_malformed_values_are_refused(value):
    assert single_mailbox(value) is None
    with pytest.raises(DomainError) as refused:
        clean_email(value, label="attendee email")
    assert refused.value.status_code == 400
    assert refused.value.detail == "Invalid attendee email"


@pytest.mark.parametrize(
    "value",
    ["lead@example.test", " lead@example.test ", "first.last+tag@sub.example.co.uk"],
)
def test_one_mailbox_is_kept_trimmed(value):
    assert single_mailbox(value) == value.strip()
    assert clean_email(value) == value.strip()


def test_blank_values_are_absent_unless_required():
    assert clean_email("  ") is None and clean_email(None) is None
    with pytest.raises(DomainError) as refused:
        clean_email("", required=True)
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "email address is required",
    )


class FakeSmtp:
    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, **kwargs):
        pass

    def login(self, *args):
        pass

    def send_message(self, msg, from_addr=None, to_addrs=None):
        FakeSmtp.sent.append((msg["To"], to_addrs))


@pytest.fixture
def smtp(monkeypatch):
    FakeSmtp.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(mailer.settings, "smtp_from_email", "keen@example.test")
    monkeypatch.setattr(mailer.settings, "smtp_use_ssl", False)
    return FakeSmtp.sent


def test_the_mailer_sends_to_exactly_one_envelope_recipient(smtp):
    mailer.send_email(to_email=" lead@example.test ", subject="S", body="B")
    assert smtp == [("lead@example.test", ["lead@example.test"])]
    # A stored list of addresses, from before this check or written directly
    # to the database, reaches nobody.
    for value in LISTS:
        mailer.send_email(to_email=value, subject="S", body="B")
    assert smtp == [("lead@example.test", ["lead@example.test"])]


def test_account_and_security_addresses_use_the_helper(monkeypatch):
    listed = SimpleNamespace(email=LISTS[0], username=LISTS[1])
    assert mailer.user_email(listed) is None
    queued = []
    monkeypatch.setattr(security_notifications, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        security_notifications, "queue_email", lambda db, **kw: queued.append(kw)
    )
    account = SimpleNamespace(id=1, username="lead", email=LISTS[0])
    security_notifications.enqueue(None, account, "password-changed", ip="192.0.2.1")
    assert queued == []
    account.email = "lead@example.test"
    security_notifications.enqueue(None, account, "password-changed", ip="192.0.2.1")
    assert [item["recipient"] for item in queued] == ["lead@example.test"]


def _email_checks(source: str, relative: str) -> list[str]:
    """Hand-rolled email tests: '"@" in x' or 'x.count("@")' outside the helper."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        ):
            if isinstance(node.left, ast.Constant) and node.left.value == "@":
                target = ast.unparse(node.comparators[0])
                if (relative, target) not in NOT_EMAIL:
                    found.append(f"{relative}:{node.lineno} {ast.unparse(node)}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "count"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "@"
        ):
            found.append(f"{relative}:{node.lineno} {ast.unparse(node)}")
    return found


def test_no_email_check_outside_the_shared_helper():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path == HELPER:
            continue
        relative = str(path.relative_to(APP))
        offenders += _email_checks(path.read_text(), relative)
    assert offenders == [], (
        "Validate addresses with app.services.text.clean_email or "
        "app.core.email_address.single_mailbox"
    )
    # The sweep finds the checks that let a list of addresses through.
    assert _email_checks('ok = "@" in value', "services/x.py") == [
        "services/x.py:1 '@' in value"
    ]
    assert _email_checks('ok = value.count("@") == 1', "services/x.py") == [
        "services/x.py:1 value.count('@')"
    ]


def test_every_smtp_send_names_its_envelope_recipients():
    tree = ast.parse((APP / "services" / "mailer.py").read_text())
    sends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"send_message", "sendmail"}
    ]
    assert len(sends) == 2
    for call in sends:
        assert any(k.arg == "to_addrs" for k in call.keywords) or len(call.args) >= 3
