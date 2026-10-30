"""In-memory Google API answers for cloud posture tests; no network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httplib2
from googleapiclient.errors import HttpError

CUSTOMER = "C0test01"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ago(days: int) -> str:
    return stamp(NOW - timedelta(days=days))


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"secret detail")


def user(name, ident, **values):
    base = {
        "id": ident,
        "primaryEmail": f"{name}@example.org",
        "isAdmin": False,
        "isDelegatedAdmin": False,
        "isEnrolledIn2Sv": True,
        "isEnforcedIn2Sv": True,
        "suspended": False,
        "archived": False,
        "lastLoginTime": ago(1),
        "creationTime": ago(400),
        "customerId": CUSTOMER,
    }
    base.update(values)
    return base


def default_users():
    return [
        user("alice", "1", isAdmin=True),
        # A delegated administrator without 2-Step Verification.
        user("bob", "2", isDelegatedAdmin=True, isEnrolledIn2Sv=False),
        # Enrolled, but not enforced.
        user("carol", "3", isEnforcedIn2Sv=False, lastLoginTime=ago(200)),
        # Never signed in.
        user(
            "dave", "4", lastLoginTime="1970-01-01T00:00:00.000Z", creationTime=ago(30)
        ),
        # Suspended and archived accounts leave every check.
        user("erin", "5", suspended=True, isEnrolledIn2Sv=False),
        user("frank", "6", archived=True, isEnrolledIn2Sv=False),
        # A shared mailbox, an administrator too.
        user(
            "shared",
            "7",
            isEnrolledIn2Sv=False,
            isEnforcedIn2Sv=False,
            isDelegatedAdmin=True,
        ),
    ]


class Request:
    def __init__(self, fake, name, kwargs):
        self.fake, self.name, self.kwargs = fake, name, kwargs

    def execute(self, num_retries=0):
        self.fake.calls.append((self.name, self.kwargs))
        failure = self.fake.fail.get(self.name)
        if failure is not None:
            raise failure
        return self.fake.answer(self.name, self.kwargs)


class Resource:
    def __init__(self, fake, path):
        self.fake, self.path = fake, path

    def __getattr__(self, name):
        path = f"{self.path}.{name}" if self.path else name

        def call(*args, **kwargs):
            if name in self.fake.methods:
                return Request(self.fake, path, kwargs)
            return Resource(self.fake, path)

        return call


class Service(Resource):
    def __init__(self, fake):
        super().__init__(fake, "")
        self.closed = False
        fake.services.append(self)

    def close(self):
        self.closed = True


class FakeDirectory:
    """The Directory API's user listing."""

    methods = {"list"}

    def __init__(self, users=None, *, page=3, fail=None):
        self.users = default_users() if users is None else users
        self.page = page
        self.fail = fail or {}
        self.calls = []
        self.made = []
        self.services = []

    def answer(self, name, kwargs):
        assert name == "users.list"
        start = int(kwargs.get("pageToken") or 0)
        out = {"users": self.users[start : start + self.page]}
        if start + self.page < len(self.users):
            out["nextPageToken"] = str(start + self.page)
        return out
