"""Enrolment refusals at the edges of a credential's life.

Covers public-subject profiles, enrolment codes whose agent was revoked or
belongs to a profile, profiles rotated or revoked while an enrolment waits for
the profile's lock, renewals replayed under a newer key, and the Valkey store
that accepts each enrolment proof once.
"""

import os
import secrets
import uuid
from contextlib import contextmanager

import app.services.agent_enrollment as agent_enrollment
import fakeredis
import pytest
from app.agents.credentials import digest
from app.core.config import settings
from app.db.models import AgentEnrollmentProfile, AgentKey, KeenAgent, UeuSettings
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.ueu_subjects import public_subject_key
from cryptography.fernet import Fernet
from sqlalchemy.orm import sessionmaker

from tests.test_agent_enrollment import enroll, firewall

# Imported fixtures are discovered by pytest by name.
from tests.test_agent_enrollment import fleet as fleet
from tests.test_keen_agent import setup as setup
from tests.ueu_agent import SimAgent, b64url

INVALID = "Invalid enrollment credential"
PROFILES = "/v1/admin/agent-enrollment-profiles"


# --- Public subject profiles -------------------------------------------------------


def test_a_public_profile_is_refused_until_its_subject_key_can_be_kept(
    fleet, monkeypatch
):
    c, f, _, _ = fleet
    with f() as db:
        before = db.query(AgentEnrollmentProfile).count()
    key = os.environ["KEEN_INTEGRATION_SECRET_KEY"]
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")
    refused = c.post(PROFILES, json={"name": "Shared", "subject_type": "public"})
    assert (refused.status_code, refused.json()) == (
        503,
        {"detail": "Public subjects need KEEN_INTEGRATION_SECRET_KEY on the API"},
    )
    with f() as db:
        assert db.query(AgentEnrollmentProfile).count() == before
        assert db.get(UeuSettings, 1) is None

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", key)
    created = c.post(PROFILES, json={"name": "Shared", "subject_type": "public"})
    assert created.status_code == 201, created.text
    with f() as db:
        # The key exists from the profile's creation, before any enrolment.
        assert db.get(UeuSettings, 1).public_subject_key.startswith("gAAAAA")
        expected = public_subject_key(db)
    enrolled = enroll(c, created.json(), name="shared-01")
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["subject"] == {"type": "public", "key": expected}


# --- Enrolment codes ---------------------------------------------------------------


def test_a_code_retried_after_its_agent_is_revoked_is_refused(fleet):
    c, f, _, _ = fleet
    registered = c.post("/v1/admin/agents", json={"name": "db-01"})
    assert registered.status_code == 201, registered.text
    code = registered.json()["enrollment_code"]
    agent = SimAgent(c)
    assert agent.enroll(code).status_code == 201
    answer = c.post(f"/v1/admin/agents/{agent.agent_id}/revoke")
    assert (answer.status_code, answer.json()) == (200, {"ok": True})

    # The spent code still matches the key it bound, so the retry reaches the
    # agent's row, where the revocation refuses it.
    retry = agent.enroll(code)
    assert (retry.status_code, retry.json()["detail"]) == (401, INVALID)
    with f() as db:
        keys = db.query(AgentKey).filter_by(agent_id=agent.agent_id).all()
        assert [k.state for k in keys] == ["revoked"]
        assert db.get(KeenAgent, agent.agent_id).enabled is False


def test_a_code_issued_to_a_profile_agent_is_refused(fleet):
    c, f, p, _ = fleet
    first = enroll(c, p)
    assert first.status_code == 201, first.text
    sim = first.sim
    rotated = c.post(f"/v1/admin/agents/{sim.agent_id}/rotate", json={})
    assert rotated.status_code == 200, rotated.text
    code = rotated.json()["enrollment_code"]

    refused = SimAgent(c).enroll(code)
    assert (refused.status_code, refused.json()["detail"]) == (401, INVALID)
    # The profile agent keeps its own key; the code bound nothing.
    assert firewall(sim).status_code == 200
    with f() as db:
        keys = db.query(AgentKey).filter_by(agent_id=sim.agent_id).all()
        assert [(k.key_id, k.state) for k in keys] == [(sim.key_id, "active")]


# --- Renewal ---------------------------------------------------------------------


def test_a_renewal_replayed_under_the_key_it_issued_is_refused(fleet):
    c, f, p, _ = fleet
    sim = enroll(c, p).sim
    renewed, new, nonce = sim.renew()
    assert renewed.status_code == 200, renewed.text
    sim.accept(renewed.json(), new)

    # The new key signs the renewal that issued it: the binding names the
    # retired key as its predecessor, so the replay is refused.
    replay, _, _ = sim.renew(nonce=nonce, new=new)
    assert (replay.status_code, replay.json()["detail"]) == (
        401,
        "Invalid agent credential",
    )
    assert firewall(sim).status_code == 200
    with f() as db:
        assert db.get(AgentKey, sim.key_id).state == "active"
        assert db.query(AgentKey).filter_by(agent_id=sim.agent_id).count() == 2


# --- Profile changes while an enrolment waits ------------------------------------------


CONTEXT = ChangeContext(method="POST", path="/v1/agents/enroll", client_ip="192.0.2.7")
# KEEN's agent keys committed by one test are read by the next.
SECRET = Fernet.generate_key().decode()


@pytest.fixture
def committed(contract_postgres_engine, monkeypatch):
    """Sessions that commit for real, so another transaction sees their work."""
    if contract_postgres_engine is None:
        pytest.skip("Requires PostgreSQL for concurrent transactions")
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(agent_enrollment, "get_valkey", lambda: None)
    monkeypatch.setattr(
        agent_enrollment, "fixed_window_allow", lambda *a, **k: (True, 0)
    )
    return sessionmaker(bind=contract_postgres_engine)


def _profile(factory):
    with factory() as db:
        created = agent_enrollment.create(
            body=agent_enrollment.ProfileInput(name="Racing " + uuid.uuid4().hex[:6]),
            context=CONTEXT,
            db=db,
        )
    return created["profile"]["id"], created["bootstrap_key"]


def _request(name="race-01"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public_key = b64url(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    return agent_enrollment.Enrollment.model_construct(
        name=name, nonce=secrets.token_urlsafe(32), public_key=public_key
    )


def test_a_profile_enrols_when_nothing_changes_while_it_waits(committed):
    ident, bootstrap = _profile(committed)
    with committed() as db:
        agent, key = agent_enrollment.enroll_with_profile(
            db, CONTEXT, bootstrap, ident, _request()
        )
        db.commit()
        assert (agent.enrollment_profile_id, key.state) == (ident, "active")


@pytest.mark.parametrize("change", ["rotate", "revoke"])
def test_a_profile_changed_while_an_enrolment_waits_for_it_is_refused(
    committed, monkeypatch, change
):
    ident, bootstrap = _profile(committed)
    waited = agent_enrollment.bounded_lock_wait

    @contextmanager
    def changed_meanwhile(db, **kwargs):
        # Another administrator's change commits after the enrolment checked
        # the key without a lock and before it takes the profile's row lock.
        with committed() as other:
            agent_enrollment.manage(
                ident=ident, action=change, context=CONTEXT, db=other
            )
        with waited(db, **kwargs):
            yield

    monkeypatch.setattr(agent_enrollment, "bounded_lock_wait", changed_meanwhile)
    with committed() as db:
        with pytest.raises(DomainError) as refused:
            agent_enrollment.enroll_with_profile(
                db, CONTEXT, bootstrap, ident, _request()
            )
        db.rollback()
    assert (refused.value.status_code, refused.value.detail) == (401, INVALID)
    with committed() as db:
        assert db.query(KeenAgent).filter_by(enrollment_profile_id=ident).count() == 0
        profile = db.get(AgentEnrollmentProfile, ident)
        assert profile.enrollment_count == 0
        assert profile.state == ("revoked" if change == "revoke" else "active")
        assert profile.key_hash != digest(bootstrap)


# --- Proof replay store ------------------------------------------------------------


def test_each_enrolment_proof_is_accepted_once_through_valkey(monkeypatch):
    store = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(agent_enrollment, "get_valkey", lambda: store)
    agent_enrollment.remember_proof("public-key", "nonce-1")
    with pytest.raises(DomainError) as used:
        agent_enrollment.remember_proof("public-key", "nonce-1")
    assert (used.value.status_code, used.value.detail) == (
        400,
        "Key proof has been used; sign a new proof and retry",
    )
    remembered = "keen:enrollment:proof:" + digest("public-key") + ":nonce-1"
    assert 0 < store.ttl(remembered) <= agent_enrollment.PROOF_NONCE_SECONDS
    agent_enrollment.remember_proof("public-key", "nonce-2")
    agent_enrollment.remember_proof("another-key", "nonce-1")


def test_enrolment_proofs_are_refused_while_valkey_is_unreachable(monkeypatch):
    def unreachable():
        raise ConnectionError("synthetic valkey outage at 192.0.2.9")

    monkeypatch.setattr(agent_enrollment, "get_valkey", unreachable)
    with pytest.raises(DomainError) as refused:
        agent_enrollment.remember_proof("public-key", "nonce-1")
    assert refused.value.status_code == 503
    assert refused.value.detail == "Enrollment unavailable; retry later"
    assert refused.value.headers == {"Retry-After": "30"}
