"""Availability through real login, CSRF and the assembled router."""

from types import SimpleNamespace

import pytest
from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
from app.security.feature_policy import requirements
from app.services import features
from app.services.errors import DomainError
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.test_assembled_authority_regressions import keen as keen  # noqa: F401
from tests.test_assembled_authority_regressions import unsafe


def test_defaults_and_parent_preferences():
    assert all(dict(features.compile_state(0, {}).effective).values())
    off = features.compile_state(
        1, {"isms": False, "swot": True, "objectives": False, "risks": False}
    )
    assert not off.enabled("swot")
    assert not off.enabled("mitigator")
    assert dict(off.configured)["swot"]
    restored = features.compile_state(2, {**dict(off.configured), "isms": True})
    assert restored.enabled("swot")
    assert not restored.enabled("objectives")


@pytest.mark.parametrize(
    "document", [{"swot": "false"}, {"unknown": False}, [], {"swot": 0}]
)
def test_corrupt_configuration_fails_closed(document):
    with pytest.raises(DomainError) as error:
        features.compile_state(1, document)
    assert error.value.status_code == 503


def test_admin_only_csrf_versions_and_preserved_settings(keen):
    admin = keen.client(keen.user(role="admin"))
    user = keen.client(keen.user("isms.manage", "risk.manage", "audits.admin"))
    anonymous = keen.client()
    for client, code in [(anonymous, 401), (user, 403)]:
        assert client.get("/v1/admin/features").status_code == code
        assert (
            client.put(
                "/v1/admin/features",
                json={"version": 0, "changes": {"isms": False}},
                headers=unsafe(client) if client is user else {},
            ).status_code
            == code
        )
    payload = {"version": 0, "changes": {"isms": False, "swot": True}}
    assert admin.put("/v1/admin/features", json=payload).status_code == 403
    response = admin.put("/v1/admin/features", json=payload, headers=unsafe(admin))
    assert response.status_code == 200, response.text
    assert response.json()["configured"]["swot"] is True
    assert response.json()["enabled"]["swot"] is False
    assert (
        admin.put("/v1/admin/features", json=payload, headers=unsafe(admin)).status_code
        == 409
    )
    assert admin.get("/v1/isms/swot").status_code == 404
    assert user.get("/v1/isms/objectives").status_code == 404
    assert admin.get("/v1/me").json()["features"]["enabled"]["isms"] is False
    assert admin.get("/v1/admin/features").status_code == 200
    assert (
        keen.db.query(ManagedConfigurationRevision).filter_by(name="features").count()
        == 1
    )
    response = admin.put(
        "/v1/admin/features",
        json={"version": 1, "changes": {"isms": True}},
        headers=unsafe(admin),
    )
    assert response.json()["enabled"]["swot"] is True
    assert admin.get("/v1/isms/swot").status_code == 200


@pytest.mark.parametrize(
    "changes,path,method",
    [
        ({"risks": False}, "/v1/risks", "GET"),
        ({"risks": False}, "/v1/risks/mitigator/analyse", "POST"),
        ({"questions": False}, "/v1/me/questions", "GET"),
        (
            {"questions": False},
            "/v1/events/00000000-0000-0000-0000-000000000001/questions",
            "POST",
        ),
        ({"audits": False}, "/v1/audits", "GET"),
        ({"swot": False}, "/v1/isms/swot", "GET"),
        ({"people": False}, "/v1/isms/people", "GET"),
        ({"effectiveness": False}, "/v1/isms/effectiveness-measures", "GET"),
        (
            {"evidence": False},
            "/v1/controls/00000000-0000-0000-0000-000000000001/evidence/summary",
            "GET",
        ),
    ],
)
def test_disabled_routes_refuse_even_admins(keen, changes, path, method):
    client = keen.client(keen.user(role="admin"))
    assert (
        client.put(
            "/v1/admin/features",
            json={"version": 0, "changes": changes},
            headers=unsafe(client),
        ).status_code
        == 200
    )
    response = client.request(method, path, headers=unsafe(client))
    assert response.status_code == 404, response.text
    assert "disabled" in response.text


def test_revision_cache_invalidates_between_sessions(contract_db):
    engine = contract_db.get_bind()
    with Session(engine) as other:
        assert features.state(other).enabled("questions")
    row = ManagedConfiguration(
        name="features", version=1, document={"questions": False}
    )
    contract_db.add(row)
    contract_db.commit()
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        with Session(engine) as other:
            assert not features.state(other).enabled("questions")
        statements.clear()
        with Session(engine) as other:
            assert not features.state(other).enabled("questions")
            assert not features.state(other).enabled("questions")
        locks = [sql for sql in statements if "pg_advisory_xact_lock_shared" in sql]
        queries = [sql for sql in statements if sql not in locks]
        assert len(locks) == (1 if engine.dialect.name == "postgresql" else 0)
        assert len(queries) == 1
        assert "managed_configurations.version" in queries[0]
        assert "managed_configurations.document" not in queries[0]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    row.document = {"questions": True}
    row.version = 2
    contract_db.commit()
    with Session(engine) as other:
        assert features.state(other).enabled("questions")


def test_route_policy_uses_owner_and_resolved_parameters():
    def endpoint():
        pass

    endpoint.__module__ = "app.api.routes.questions"
    route = SimpleNamespace(
        endpoint=endpoint, path="/v1/questions/entities/{entity_type}/{entity_id}"
    )
    assert requirements(route, {"entity_type": "isms_swot"}) == {"questions", "swot"}
    endpoint.__module__ = "app.api.routes.features"
    route.path = "/v1/admin/features"
    assert not requirements(route)


def test_sampling_and_worker_paths_obey_features(keen):
    from app.services.audit_schedules import materialize_due_scheduled_audits
    from app.services.audits.operations import add_audit_evidence
    from app.services.effectiveness_metrics import (
        materialize_missing_monthly_zero_metrics,
    )

    user = keen.user(role="admin")
    features.update(
        keen.db,
        user,
        0,
        {"event_sampling": False, "audit_scheduling": False, "effectiveness": False},
    )
    with pytest.raises(DomainError) as rejected:
        add_audit_evidence(
            audit_id="unused",
            payload=SimpleNamespace(event_id="event"),
            db=keen.db,
            user=user,
        )
    assert rejected.value.status_code == 404
    assert materialize_due_scheduled_audits(keen.db)["enabled"] is False
    assert materialize_missing_monthly_zero_metrics(keen.db)["enabled"] is False


def test_disabled_entity_cannot_be_sampled_or_linked(keen):
    from app.services.audits.access import require_sample_source_access
    from app.services.isms.swot import _target

    user = keen.user(role="admin")
    features.update(keen.db, user, 0, {"assets": False})
    with pytest.raises(DomainError):
        require_sample_source_access(keen.db, user, "isms_asset", None)
    with pytest.raises(DomainError):
        _target(keen.db, user, "asset", None)
    assert _target(keen.db, user, "asset", None, required=False) is None


def test_invalid_settings_do_not_write(keen):
    client = keen.client(keen.user(role="admin"))
    for changes in ({"swot": 0}, {"swot": "false"}, {"unknown": False}, {}):
        response = client.put(
            "/v1/admin/features",
            json={"version": 0, "changes": changes},
            headers=unsafe(client),
        )
        assert response.status_code == 422
    assert keen.db.get(ManagedConfiguration, "features") is None
    assert (
        keen.db.query(ManagedConfigurationRevision).filter_by(name="features").count()
        == 0
    )


def test_concurrent_initial_saves_have_one_winner(contract_postgres_engine):
    if contract_postgres_engine is None:
        pytest.skip("Requires disposable PostgreSQL: locking and unique-key contention")
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.db.models import User

    engine = contract_postgres_engine
    with Session(engine) as db:
        user = User(
            username="features-race-" + uuid.uuid4().hex,
            password_hash="unused-synthetic-test-hash",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    start = Barrier(2)

    def save(key):
        with Session(engine) as db:
            actor = db.get(User, user_id)
            start.wait(timeout=10)
            try:
                return features.update(db, actor, 0, {key: False})["version"]
            except DomainError as error:
                return error.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(save, ["swot", "questions"])) == [1, 409]
        with Session(engine) as db:
            assert db.get(ManagedConfiguration, "features").version == 1
            assert (
                db.query(ManagedConfigurationRevision)
                .filter_by(name="features")
                .count()
                == 1
            )
    finally:
        with Session(engine) as db:
            db.query(ManagedConfigurationRevision).filter_by(name="features").delete()
            db.query(ManagedConfiguration).filter_by(name="features").delete()
            db.query(User).filter_by(id=user_id).delete()
            db.commit()


def test_every_router_has_an_availability_decision():
    from app.api.dependencies.features import enforce_features
    from app.main import app
    from app.security.feature_policy import ISMS_OWNERS, OWNERS
    from fastapi.routing import APIRoute

    core = {
        "admin",
        "agent_enrollment",
        "agents",
        "app.main",
        "auth",
        "evidence_retention",
        "features",
        "frameworks",
        "integrations",
        "managed_configurations",
        "me",
        "mfa",
        "source_connections",
        "sso_emails",
        "users",
        "webhooks",
        "stats",
        # Published UEU catalogues are vocabularies, available with any feature set.
        "ueu",
    }
    seen = []

    def walk(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                owner = route.endpoint.__module__.removeprefix("app.api.routes.")
                if owner.startswith("isms."):
                    assert owner.split(".")[-1] in set(ISMS_OWNERS) | {
                        "overview",
                        "personal",
                        "relationships",
                    }
                    assert "isms" in requirements(route)
                else:
                    assert owner in core | set(OWNERS) | {"assurance"}, owner
                if requirements(route):
                    assert any(
                        d.dependency is enforce_features for d in route.dependencies
                    ), route.path
                seen.append(route.path)
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)

    walk(app.routes)
    assert len(seen) > 300


def test_normalised_entity_route_cannot_bypass_disabled_child(keen):
    client = keen.client(keen.user(role="admin"))
    response = client.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {"swot": False}},
        headers=unsafe(client),
    )
    assert response.status_code == 200
    for entity in ("swot", "SWOT"):
        response = client.get(
            f"/v1/isms/{entity}/00000000-0000-0000-0000-000000000001/changelog"
        )
        assert response.status_code == 404
        assert "disabled" in response.text
