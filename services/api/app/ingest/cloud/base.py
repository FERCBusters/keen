"""Shapes shared by the cloud vendor posture providers.

A provider reads one vendor's API with read-only credentials from the
deployment environment. For each account, subscription or project it can see
it returns a short list of checks and the identities the vendor lists, and it
may also read the vendor's recent activity. KEEN stores the latest result for
each scope, records every check result and activity entry as an event, and
shows both on the vendor's page.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# met: the setting meets the check; attention: it does not; info: reported
# without a judgement; unknown: KEEN could not read it (see ``reason``).
STATUSES = ("met", "attention", "info", "unknown")
REASONS = {
    "permission_denied": "KEEN's credentials are not allowed to read this.",
    "not_licensed": "The vendor's plan for this account does not include it.",
    "not_configured": "It is not set up for this account, or the credentials "
    "were not accepted.",
    "unavailable": "The vendor did not answer, or answered with an error.",
}
# Bounds on what one refresh stores for a provider.
MAX_SCOPES = 100
MAX_SUBJECTS = 50
MAX_IDENTITIES = 1000
MAX_ACTIVITY = 1000
TEXT_LIMIT = 256


class Unavailable(Exception):
    """A vendor read failed; carries a reason code and never provider text."""

    def __init__(self, reason: str = "unavailable"):
        self.reason = reason if reason in REASONS else "unavailable"
        super().__init__(self.reason)


def clip(value, limit: int = TEXT_LIMIT) -> str:
    return str(value or "")[:limit]


@dataclass
class Check:
    key: str
    label: str
    status: str
    detail: str = ""
    # Names of the identities or resources that need attention.
    subjects: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        names = sorted({clip(s) for s in self.subjects if s})
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status if self.status in STATUSES else "unknown",
            "detail": clip(self.detail, 512),
            "reason": self.reason,
            "subjects": names[:MAX_SUBJECTS],
            "subjects_total": len(names),
        }


@dataclass
class Identity:
    """One user, role or key the vendor lists for a scope."""

    name: str
    email: str = ""
    kind: str = "user"  # user, service, root, guest
    admin: bool = False
    mfa: bool | None = None
    last_used: datetime | None = None
    created: datetime | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        def iso(value):
            return value.replace(tzinfo=None).isoformat() if value else None

        return {
            "name": clip(self.name),
            "email": clip(self.email),
            "kind": self.kind,
            "admin": bool(self.admin),
            "mfa": self.mfa,
            "last_used": iso(self.last_used),
            "created": iso(self.created),
            "detail": clip(self.detail),
        }


@dataclass
class ScopeResult:
    key: str
    label: str
    checks: list[Check]
    identities: list[Identity] = field(default_factory=list)
    # What the scope is, such as "tenant", when it differs from the
    # provider's usual scope.
    kind: str = ""


@dataclass
class Activity:
    """One entry from a vendor's activity or audit log."""

    id: str
    at: datetime
    scope: str
    action: str
    actor: str = ""
    outcome: str = "info"  # success, failure or info
    summary: str = ""
    detail: dict = field(default_factory=dict)
    # The vendor's own record, kept as the event's artifact.
    raw: dict | str = field(default_factory=dict)


@dataclass(frozen=True)
class Provider:
    key: str  # settings prefix and event source
    label: str
    vendor: str
    website: str
    scope_noun: str  # "account", "subscription", ...
    checks: dict  # check key -> label
    collect: Callable[[], list[ScopeResult]]
    # activity(since, until) -> (entries in [since, until), oldest first;
    # whether every entry in the window was read). At most MAX_ACTIVITY.
    activity: Callable[[datetime, datetime], tuple[list[Activity], bool]] | None = None
    # How long the vendor takes to make an activity entry readable.
    activity_lag: timedelta = timedelta(minutes=5)
    aliases: tuple = ()
    # The setting that turns the provider on, when it is not ``<key>_enabled``.
    enabled_setting: str = ""

    @property
    def setting(self) -> str:
        return self.enabled_setting or self.key + "_enabled"


def unknown(checks: dict, keys, reason: str) -> list[Check]:
    return [
        Check(k, checks[k], "unknown", REASONS[reason], reason=reason) for k in keys
    ]


def guarded(checks: dict, keys, collect: Callable[[], list[Check]]) -> list[Check]:
    """Run one group of checks. When the vendor refuses or fails the read,
    each check in the group reads as not established, with the reason."""
    try:
        return collect()
    except Unavailable as exc:
        return unknown(checks, keys, exc.reason)


def plural(count: int, noun: str, plural_noun: str | None = None) -> str:
    return f"{count} {noun if count == 1 else plural_noun or noun + 's'}"


def mfa_check(key, label, people: list[Identity], *, who="users") -> Check:
    """Every listed identity has MFA; identities whose MFA is unknown are
    left out of the count. ``who`` is a plural noun."""
    known = [p for p in people if p.mfa is not None]
    missing = [p.name for p in known if not p.mfa]
    if not known:
        return Check(key, label, "info", f"No {who} to check")
    noun = who if len(known) != 1 else who[:-1]
    if missing:
        return Check(
            key,
            label,
            "attention",
            f"MFA off for {len(missing)} of {len(known)} {noun}",
            missing,
        )
    return Check(key, label, "met", f"MFA on for {len(known)} of {len(known)} {noun}")


def inactive_check(key, label, people: list[Identity], days: int, now) -> Check:
    """Identities with no recorded use within ``days``; one never used is
    judged by when it was created."""
    stale = []
    for person in people:
        last = person.last_used or person.created
        if last is not None and (now - last.replace(tzinfo=None)).days >= days:
            stale.append(person.name)
    if not people:
        return Check(key, label, "info", "No users to check")
    detail = (
        f"{len(stale)} of {plural(len(people), 'user')} unused for {days} days or more"
    )
    return Check(key, label, "attention" if stale else "met", detail, stale)
