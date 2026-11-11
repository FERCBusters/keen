"""LDAP trust boundaries, role ownership, and round-trippable rule exports."""

import logging
import re
import ssl
import uuid
from types import SimpleNamespace

import app.services.login as login_operations
import app.services.risks as risks_operations
import fakeredis
import pytest
import yaml
from app.api.routes import managed_configurations as mappings
from app.api.routes import risks
from app.core.config import settings
from app.db.models import (
    IsmsOrgNode,
    IsmsOrgNodeUser,
    ManagedConfiguration,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from app.mapping.rules import parse_rules
from app.security import ldap, mfa
from app.security.auth import authenticate_user
from app.security.auth import cookie_name as session_cookie_name
from app.services.errors import DomainError
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.db_helpers import create_sqlite_schema

LDAP_UNAVAILABLE = (
    "LDAP authentication unavailable; check directory configuration and connectivity"
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_sqlite_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def directory(monkeypatch):
    for key, value in dict(
        ldap_enabled=True,
        ldap_url="ldap://directory.example:389",
        ldap_base_dn="ou=People,dc=example",
        ldap_bind_dn="cn=reader,dc=example",
        ldap_bind_password="search-secret",
        ldap_username_attribute="uid",
        ldap_email_attribute="mail",
        ldap_id_attribute="entryUUID",
        ldap_auto_provision=True,
        ldap_group_filter="",
        ldap_group_base_dn="",
        ldap_group_member_attribute="member",
    ).items():
        monkeypatch.setattr(settings, key, value)
    events = []
    connections = []
    state = {
        "tls": True,
        "bind": True,
        "entries": [
            SimpleNamespace(
                entry_dn="uid=alice,ou=People,dc=example",
                entry_attributes_as_dict={
                    "uid": ["alice"],
                    "mail": ["alice@example.org"],
                    "entryUUID": ["immutable-id"],
                },
            )
        ],
    }

    class FakeConnection:
        def __init__(self, server, **kw):
            self.kw = kw
            self.closed = True
            self.result = {"result": 0}
            connections.append(self)

        def open(self):
            self.closed = False
            events.append("open")

        def start_tls(self):
            events.append("tls")
            return state["tls"]

        def bind(self):
            events.append(("bind", self.kw["user"]))
            if self.kw["user"].startswith("uid=") and not state["bind"]:
                self.result = {"result": 49}
                return False
            if not self.kw["user"].startswith("uid=") and "service_bind" in state:
                self.result = {"result": state["service_bind"]}
                return False
            if self.kw["user"].startswith("uid=") and "user_bind" in state:
                self.result = {"result": state["user_bind"]}
                return False
            return True

        def search(self, base, query, **kw):
            state.setdefault("searched_as", []).append(self.kw["user"])
            if kw["attributes"] == ldap.NO_ATTRIBUTES:
                # The group lookup asks for no attributes.
                state["group_search"] = (base, query, kw)
                self.entries = state.get("groups", [])
                self.result = {"result": state.get("group_result", 0)}
                return True
            state["search"] = (base, query, kw)
            self.entries = state["entries"]
            self.result = {"result": state.get("result", 0)}
            return True

        def unbind(self):
            events.append("unbind")

    monkeypatch.setattr(ldap, "Connection", FakeConnection)
    # Each test counts directory-entry attempts in its own store.
    store = fakeredis.FakeRedis()
    monkeypatch.setattr(ldap, "get_valkey", lambda: store)
    return state, events, connections


def test_starttls_filter_escaping_and_ou(directory):
    state, events, connections = directory
    result = ldap.directory_identity("alice*)(uid=*)", "password")
    assert result["subject"] == "immutable-id"
    assert events[:3] == ["open", "tls", ("bind", "cn=reader,dc=example")]
    assert events[3:6] == ["open", "tls", ("bind", "uid=alice,ou=People,dc=example")]
    base, query, kw = state["search"]
    assert base == settings.ldap_base_dn and r"alice\2a\29\28uid=\2a\29" in query
    assert kw["size_limit"] == 2 and kw["dereference_aliases"] == ldap.DEREF_NEVER
    assert all(
        c.kw["auto_referrals"] is False and c.kw["read_only"] for c in connections
    )


def test_ldaps_cert_validation(directory, monkeypatch):
    monkeypatch.setattr(settings, "ldap_url", "ldaps://directory.example:636")
    real = ldap.Server
    seen = []
    monkeypatch.setattr(
        ldap, "Server", lambda *a, **kw: seen.append(kw) or real(*a, **kw)
    )
    assert ldap.directory_identity("alice", "secret") == {
        "issuer": "ldap:ldaps://directory.example:636",
        "subject": "immutable-id",
        "username": "alice",
        "email": "alice@example.org",
    }
    assert (seen[0]["use_ssl"], seen[0]["tls"].validate) == (True, ssl.CERT_REQUIRED)
    assert "tls" not in directory[1]


def test_failed_starttls_never_binds(directory):
    directory[0]["tls"] = False
    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")
    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    assert directory[1] == ["open", "tls", "unbind"]


@pytest.mark.parametrize("case", ["ambiguous", "missing-id", "partial"])
def test_directory_denials(directory, case):
    state = directory[0]
    if case == "ambiguous":
        state["entries"] *= 2
    if case == "missing-id":
        state["entries"][0].entry_attributes_as_dict.pop("entryUUID")
    if case == "partial":
        state["result"] = 4
    assert ldap.directory_identity("alice", "secret") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"ldap_url": "https://directory.example"},
        {"ldap_url": "ldap://"},
        {"ldap_url": "ldap://reader:secret@directory.example"},
        {"ldap_url": "ldap://directory.example/dc=example"},
        {"ldap_url": "ldap://directory.example?uid"},
        {"ldap_url": "ldap://directory.example#top"},
        {"ldap_base_dn": "   "},
        {"ldap_base_dn": "not a distinguished name"},
        {"ldap_username_attribute": "uid)(objectClass=*"},
        {"ldap_email_attribute": "mail;binary"},
        {"ldap_id_attribute": "1entryUUID"},
        {"ldap_bind_password": ""},
        {"ldap_bind_dn": ""},
    ],
    ids=[
        "scheme",
        "no-host",
        "embedded-credentials",
        "path",
        "query",
        "fragment",
        "blank-base",
        "malformed-base",
        "filter-in-attribute",
        "option-in-attribute",
        "attribute-digit",
        "dn-without-password",
        "password-without-dn",
    ],
)
def test_unsafe_directory_settings_refuse_sign_in_before_connecting(
    directory, monkeypatch, changes
):
    for key, value in changes.items():
        monkeypatch.setattr(settings, key, value)

    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")

    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    assert directory[1] == []


POSIX_GROUPS = "(&(objectClass=posixGroup)(|(cn=staff)(cn=auditors)))"
GROUP = SimpleNamespace(entry_dn="cn=staff,ou=Groups,dc=example")


def test_without_a_group_filter_no_group_search_runs(directory):
    assert ldap.directory_identity("alice", "secret")["subject"] == "immutable-id"
    assert "group_search" not in directory[0]


def test_a_posix_group_member_signs_in_by_the_directory_username(
    directory, monkeypatch
):
    state, events, _ = directory
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    monkeypatch.setattr(settings, "ldap_group_member_attribute", "memberUid")
    state["groups"] = [GROUP]

    # memberUid matches exactly, so the directory's spelling is sent, not the typed one.
    assert ldap.directory_identity(" ALICE ", "secret")["username"] == "alice"

    base, query, kw = state["group_search"]
    assert base == settings.ldap_base_dn
    assert query == f"(&{POSIX_GROUPS}(memberUid=alice))"
    assert kw["size_limit"] == 1 and kw["dereference_aliases"] == ldap.DEREF_NEVER
    assert kw["time_limit"] == settings.ldap_timeout_seconds
    # Both searches use the service account, before the user's password is sent.
    assert state["searched_as"] == ["cn=reader,dc=example"] * 2
    assert events.count(("bind", "uid=alice,ou=People,dc=example")) == 1


def test_a_dn_valued_group_attribute_is_escaped_and_searched_below_its_own_base(
    directory, monkeypatch
):
    state = directory[0]
    monkeypatch.setattr(settings, "ldap_group_filter", "(objectClass=groupOfNames)")
    monkeypatch.setattr(settings, "ldap_group_base_dn", "ou=Groups,dc=example")
    state["entries"][0].entry_dn = "uid=al(ice)*,ou=People,dc=example"
    state["entries"][0].entry_attributes_as_dict["uid"] = ["al*)(cn=*"]
    state["groups"] = [GROUP]

    assert ldap.directory_identity("alice", "secret") is not None

    base, query, _ = state["group_search"]
    assert base == "ou=Groups,dc=example"
    assert query == (
        "(&(objectClass=groupOfNames)"
        r"(member=uid=al\28ice\29\2a,ou=People,dc=example))"
    )


def test_a_hostile_directory_username_cannot_widen_the_group_filter(
    directory, monkeypatch
):
    state = directory[0]
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    monkeypatch.setattr(settings, "ldap_group_member_attribute", "MemberUID")
    state["entries"][0].entry_attributes_as_dict["uid"] = ["al*)(cn=*"]
    state["groups"] = [GROUP]

    ldap.directory_identity("alice", "secret")

    assert state["group_search"][1] == (
        f"(&{POSIX_GROUPS}" r"(MemberUID=al\2a\29\28cn=\2a))"
    )


def test_someone_outside_the_groups_is_refused_before_their_password_is_tried(
    directory, db, monkeypatch
):
    state, events, _ = directory
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    monkeypatch.setattr(settings, "ldap_group_member_attribute", "memberUid")
    store = ldap.get_valkey()

    # The same answer as an unknown name: no account, no bind, no entry attempt.
    assert ldap.authenticate_ldap(db, "alice", "secret") is None
    assert db.query(User).count() == 0
    assert ("bind", "uid=alice,ou=People,dc=example") not in events
    assert store.keys("*") == []

    # Membership is read at each password check, so joining a group admits them
    # and leaving it refuses the next sign-in and reauthentication.
    state["groups"] = [GROUP]
    user = ldap.authenticate_ldap(db, "alice", "secret")
    assert user.username == "ldap:alice"
    assert ldap.verify_ldap_user(db, user, "secret") is True
    state["groups"] = []
    assert ldap.authenticate_ldap(db, "alice", "secret") is None
    assert ldap.verify_ldap_user(db, user, "secret") is False


def test_a_member_of_several_matching_groups_is_admitted(directory, monkeypatch):
    state = directory[0]
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    # The directory returns the first group and reports that more matched.
    state["groups"] = [GROUP]
    state["group_result"] = 4
    assert ldap.directory_identity("alice", "secret") is not None
    state["groups"] = []
    assert ldap.directory_identity("alice", "secret") is None


@pytest.mark.parametrize("result", [1, 10, 32, 50])
def test_a_group_search_the_directory_cannot_answer_is_not_a_refusal(
    directory, monkeypatch, result
):
    state, events, _ = directory
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    # A referral, a missing base or denied access says nothing about membership,
    # even when entries came back with it.
    state["groups"] = [GROUP]
    state["group_result"] = result
    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")
    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    assert ("bind", "uid=alice,ou=People,dc=example") not in events


@pytest.mark.parametrize(
    "changes",
    [
        {"ldap_group_member_attribute": "member)(cn=*"},
        {"ldap_group_member_attribute": ""},
        {"ldap_group_base_dn": "not a distinguished name"},
    ],
    ids=["filter-in-attribute", "no-attribute", "malformed-group-base"],
)
def test_unsafe_group_settings_refuse_sign_in_before_connecting(
    directory, monkeypatch, changes
):
    monkeypatch.setattr(settings, "ldap_group_filter", POSIX_GROUPS)
    for key, value in changes.items():
        monkeypatch.setattr(settings, key, value)

    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")

    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    assert directory[1] == []


def test_the_log_names_why_directory_sign_in_is_unavailable_without_directory_data(
    directory, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger="app.security.ldap")
    monkeypatch.setattr(settings, "ldap_bind_dn", "")
    with pytest.raises(HTTPException):
        ldap.directory_identity("alice", "secret")
    assert caplog.messages == [
        "LDAP sign-in unavailable: Supply both service bind DN and password"
    ]

    # A library or network failure may quote DNs, hosts or server diagnostics:
    # only its type is logged, and the response stays the fixed message.
    caplog.clear()
    monkeypatch.setattr(settings, "ldap_bind_dn", "cn=reader,dc=example")

    def failing_search(self, base, query, **kw):
        raise ldap.LDAPException(
            "uid=alice,ou=People,dc=example refused: synthetic-diagnostic"
        )

    monkeypatch.setattr(ldap.Connection, "search", failing_search)
    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")
    assert error.value.detail == LDAP_UNAVAILABLE
    assert caplog.messages == ["LDAP sign-in unavailable: LDAPException"]
    assert "alice" not in caplog.text and "synthetic-diagnostic" not in caplog.text
    assert "search-secret" not in caplog.text


@pytest.mark.parametrize("result", [49, 52])
def test_a_refused_service_bind_makes_directory_sign_in_unavailable(directory, result):
    directory[0]["service_bind"] = result
    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")
    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    # The search never runs and the user's password is never sent.
    assert "search" not in directory[0]
    assert ("bind", "uid=alice,ou=People,dc=example") not in directory[1]


def test_a_directory_failure_during_the_user_bind_is_not_a_wrong_password(directory):
    # Only result 49 (invalid credentials) denies the user; anything else means
    # the directory could not answer.
    directory[0]["user_bind"] = 52
    with pytest.raises(HTTPException) as error:
        ldap.directory_identity("alice", "secret")
    assert (error.value.status_code, error.value.detail) == (503, LDAP_UNAVAILABLE)
    directory[0]["user_bind"] = 49
    assert ldap.directory_identity("alice", "secret") is None


def test_directory_attributes_are_read_as_single_values_whatever_their_shape(
    directory,
):
    attributes = directory[0]["entries"][0].entry_attributes_as_dict
    attributes.update(uid="alice", mail=["alice@example.org"], entryUUID=[b"\x01\x02"])

    identity = ldap.directory_identity("alice", "secret")

    assert identity["username"] == "alice"
    # A binary identifier, such as objectGUID, is kept as base64 text.
    assert identity["subject"] == "AQI="
    attributes.update(uid=["alice", "alicia"])
    assert ldap.directory_identity("alice", "secret") is None


def test_a_new_directory_user_never_takes_an_existing_account_name(db, directory):
    taken = User(username="ldap:alice", password_hash="local-hash", role="admin")
    db.add(taken)
    db.commit()

    user = ldap.authenticate_ldap(db, "alice", "password")

    assert user.id != taken.id
    assert re.fullmatch("ldap:[0-9a-f]{32}", user.username)
    assert (user.role, user.auth_backend) == ("normal", "ldap")
    from app.db.models import IsmsPerson

    person = db.query(IsmsPerson).filter_by(user_id=user.id).one()
    assert person.name == user.username
    # The same directory identity returns to the same account afterwards.
    assert ldap.authenticate_ldap(db, "alice", "password").id == user.id
    assert db.query(IsmsPerson).filter_by(user_id=user.id).count() == 1
    assert ldap.verify_ldap_user(db, taken, "password") is False


def test_empty_password_never_connects(directory):
    assert ldap.directory_identity("alice", "") is None
    assert directory[1] == []


def test_directory_cannot_take_over_local_user(db, directory):
    admin = User(
        username="alice",
        email="alice@example.org",
        password_hash="local-hash",
        role="admin",
    )
    db.add(admin)
    db.commit()
    user = ldap.authenticate_ldap(db, "alice", "password")
    assert user.id != admin.id and user.role == "normal" and user.auth_backend == "ldap"
    assert user.password_hash == "!LDAP" and user.username == "ldap:alice"
    assert ldap.authenticate_ldap(db, "alice", "password").id == user.id
    assert authenticate_user(db, user.username, "password") is None
    user.is_active = False
    db.commit()
    assert ldap.authenticate_ldap(db, "alice", "password") is None


def test_no_auto_provision(db, directory, monkeypatch):
    monkeypatch.setattr(settings, "ldap_auto_provision", False)
    assert ldap.authenticate_ldap(db, "alice", "password") is None
    assert db.query(User).count() == 0


def test_reauthentication_requires_same_directory_identity(db, directory):
    user = ldap.authenticate_ldap(db, "alice", "password")
    assert ldap.verify_ldap_user(db, user, "password") is True
    directory[0]["entries"][0].entry_attributes_as_dict["entryUUID"] = ["different-id"]
    assert ldap.verify_ldap_user(db, user, "password") is False


def test_ldap_session_respects_mfa(db, directory, monkeypatch):
    user = ldap.authenticate_ldap(db, "alice", "password")
    user.mfa_enabled = True
    db.commit()
    monkeypatch.setattr(settings, "mfa_policy", "all")
    assert (
        mfa.local_session_allowed(
            db, user, {"auth_method": "ldap", "mfa_version": user.mfa_version}
        )
        is False
    )
    assert (
        mfa.local_session_allowed(
            db,
            user,
            {
                "auth_method": "ldap",
                "mfa_version": user.mfa_version,
                "mfa_verified": True,
            },
        )
        is True
    )
    assert not mfa.local_session_allowed(
        db,
        user,
        {
            "auth_method": "ldap",
            "mfa_version": user.mfa_version + 1,
            "mfa_verified": True,
        },
    )


@pytest.fixture
def role_risk(db):
    user = User(username="owner", password_hash="test", role="normal", is_active=True)
    role = IsmsOrgNode(name="Security lead", node_type="role")
    category = RiskCategory(name="IT")
    db.add_all([user, role, category])
    db.flush()
    sub = RiskAssetSubcategory(name="Servers", category_id=category.id)
    db.add(sub)
    db.flush()
    asset = RiskAsset(name="Server", category_id=category.id, subcategory_id=sub.id)
    db.add(asset)
    db.flush()
    risk = Risk(asset_id=asset.id)
    db.add(risk)
    db.commit()
    return user, role, risk


def test_role_owner_assignment_access_and_clearing(db, role_risk):
    user, role, risk = role_risk
    risks_operations._apply_payload(
        db, risk, risks.RiskUpsertPayload(risk_owner_role_id=role.id), is_create=False
    )
    db.commit()
    assert risk.risk_owner_user_id is None and risk.risk_owner_role_id == role.id
    assert risks_operations.risk_owner_out(risk) == {
        "id": str(role.id),
        "type": "role",
        "name": "Security lead",
        "username": "Security lead (role)",
    }
    assert risks_operations._is_risk_owner(user, risk) is False
    db.add(IsmsOrgNodeUser(org_node_id=role.id, user_id=user.id))
    db.commit()
    assert risks_operations._is_risk_owner(user, risk) is True
    assert risks.list_my_owned_risks(user=user, db=db)["total"] == 1
    risks_operations._apply_payload(
        db, risk, risks.RiskUpsertPayload(note="Keep owner"), is_create=False
    )
    db.commit()
    assert risk.risk_owner_role_id == role.id
    risks_operations._apply_payload(
        db, risk, risks.RiskUpsertPayload(risk_owner_user_id=user.id), is_create=False
    )
    db.commit()
    assert risk.risk_owner_role_id is None and risk.risk_owner_user_id == user.id
    risks_operations._apply_payload(
        db, risk, risks.RiskUpsertPayload(risk_owner_role_id=None), is_create=False
    )
    db.commit()
    assert (risk.owner, risk.owner_role) == (None, None)


def test_owner_validation(db, role_risk):
    user, role, risk = role_risk
    for fields, message in [
        (
            dict(risk_owner_role_id=role.id, risk_owner_user_id=user.id),
            "Choose one owner: a user or an organisational role",
        ),
        (dict(risk_owner_role_id=uuid.uuid4()), "Unknown organisational role"),
    ]:
        with pytest.raises(DomainError, match=f"^{message}$") as refused:
            risks_operations._apply_payload(
                db, risk, risks.RiskUpsertPayload(**fields), is_create=False
            )
        assert refused.value.status_code == 400
    role.node_type = "department"
    db.commit()
    # A department is no role, so it cannot own a risk.
    with pytest.raises(DomainError, match="^Unknown organisational role$"):
        risks_operations._apply_payload(
            db,
            risk,
            risks.RiskUpsertPayload(risk_owner_role_id=role.id),
            is_create=False,
        )


def test_yaml_export_roundtrip(db):
    rules = [
        {
            "id": "patching",
            "description": "Package changes",
            "when": {
                "source": "dpkg",
                "action_regex": "^package\\.",
                "fields": [
                    {
                        "path": ["fields", "package"],
                        "operator": "equals",
                        "value": "openssl",
                    }
                ],
            },
            "map_to": [{"framework": "ISO27001:2022", "ref": "A.8.8"}],
            "confidence": 0.9,
        },
        {
            "id": "paused",
            "when": {"source": "dpkg"},
            "enabled": False,
            "map_to": [{"framework": "ISO27001:2022", "ref": "A.8.8"}],
        },
        {
            "id": "rss",
            "when": {"source": "rss"},
            "map_to": [{"framework": "ISO27001:2022", "ref": "A.5.19"}],
        },
    ]
    db.add(ManagedConfiguration(name="rules", document={"rules": rules}, version=1))
    db.commit()
    response = mappings.export_rules("dpkg", db)
    doc = yaml.safe_load(response.body)
    assert [r["id"] for r in doc["rules"]] == ["patching", "paused"]
    assert doc["rules"][1]["enabled"] is False
    assert parse_rules(doc) == parse_rules({"rules": rules[:2]})
    assert [
        r["id"] for r in yaml.safe_load(mappings.export_rules("", db).body)["rules"]
    ] == [
        "patching",
        "paused",
        "rss",
    ]
    assert yaml.safe_load(mappings.export_rules("no-such-source", db).body) == {
        "rules": []
    }
    assert response.headers["cache-control"] == "no-store"


def test_ldap_login_route_and_mfa_with_local_disabled(db, directory, monkeypatch):
    import json

    import fakeredis
    from app.api.routes import auth
    from app.db.models import MfaChallenge
    from app.schemas import LoginPayload
    from app.security.sessions import get_session
    from starlette.requests import Request

    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(login_operations, "get_valkey", lambda: redis)
    monkeypatch.setattr(auth, "get_valkey", lambda: redis)
    monkeypatch.setattr(settings, "local_auth_enabled", False)
    monkeypatch.setattr(settings, "mfa_policy", "off")
    monkeypatch.setattr(settings, "public_base_url", "https://keen.example")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("keen.example", 443),
            "path": "/v1/auth/login",
            "query_string": b"",
            "headers": [(b"origin", b"https://keen.example")],
            "client": ("192.0.2.1", 1000),
        }
    )
    response = auth.login(
        LoginPayload(username="alice", password="password", method="ldap"), request, db
    )
    assert (response.status_code, json.loads(response.body)) == (
        200,
        {"user": "ldap:alice", "user_id": str(db.query(User).one().id)},
    )
    from http.cookies import SimpleCookie

    cookie = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        cookie.load(header)
    session = get_session(redis, cookie[session_cookie_name()].value)
    assert session["auth_method"] == "ldap"
    user = db.query(User).filter_by(auth_backend="ldap").one()
    user.mfa_enabled = True
    db.commit()
    response = auth.login(
        LoginPayload(username="alice", password="password", method="ldap"), request, db
    )
    assert json.loads(response.body)["mfa_required"] is True
    assert (
        db.query(MfaChallenge).filter_by(user_id=user.id, stage="verify").count() == 1
    )
    # With local sign-in off, a password sign-in that names no method is refused.
    with pytest.raises(HTTPException) as error:
        auth.login(LoginPayload(username="alice", password="password"), request, db)
    assert (error.value.status_code, error.value.detail) == (
        404,
        "Login method is disabled",
    )


def test_organisation_framework_selection(db, monkeypatch):
    from app.api.routes import frameworks
    from app.db.models import Framework

    monkeypatch.setattr(settings, "enabled_frameworks", "")
    user = User(username="admin", password_hash="test", role="admin")
    db.add(user)
    db.add_all([Framework(slug="ONE", name="One"), Framework(slug="TWO", name="Two")])
    db.commit()
    before = frameworks.list_frameworks(db)
    assert [item["slug"] for item in before["items"]] == ["ONE", "TWO"]
    selected = frameworks.save_framework_selection(
        frameworks.FrameworkSelectionInput(enabled=["TWO"], default="TWO", version=0),
        user,
        db,
    )
    assert selected["version"] == 1
    assert frameworks.list_frameworks(db)["default"] == "TWO"
    assert [item["slug"] for item in frameworks.list_frameworks(db)["items"]] == ["TWO"]
    assert db.query(Framework).count() == 2
    # The selection lists every framework and whether it is enabled.
    assert [(item["slug"], item["enabled"]) for item in selected["items"]] == [
        ("ONE", False),
        ("TWO", True),
    ]
    with pytest.raises(
        DomainError,
        match="^Configuration changed in another session. Reload before saving.$",
    ) as error:
        frameworks.save_framework_selection(
            frameworks.FrameworkSelectionInput(
                enabled=["ONE"], default="ONE", version=0
            ),
            user,
            db,
        )
    assert error.value.status_code == 409
    monkeypatch.setattr(settings, "enabled_frameworks", "ONE")
    # Environment change chooses a valid default without exposing excluded frameworks.
    assert frameworks.list_frameworks(db)["default"] == "ONE"
    with pytest.raises(
        DomainError,
        match="^A selected framework is excluded by KEEN_ENABLED_FRAMEWORKS$",
    ) as excluded:
        frameworks.save_framework_selection(
            frameworks.FrameworkSelectionInput(
                enabled=["TWO"], default="TWO", version=1
            ),
            user,
            db,
        )
    assert excluded.value.status_code == 400
    with pytest.raises(
        DomainError, match="^The default framework must be selected$"
    ) as unselected:
        frameworks.save_framework_selection(
            frameworks.FrameworkSelectionInput(
                enabled=["ONE"], default="TWO", version=1
            ),
            user,
            db,
        )
    assert unselected.value.status_code == 400
    monkeypatch.setattr(settings, "enabled_frameworks", "TYPO")
    assert frameworks.list_frameworks(db)["items"] == []
    assert frameworks.framework_selection(db)["unknown_environment_slugs"] == ["TYPO"]
