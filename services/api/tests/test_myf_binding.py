"""KEEN's binding to myf: KEEN names itself, plays every role, and myf keeps
its safe defaults wherever an application leaves a hook out."""

import app.myf_hooks as keen_hooks
import pytest
from myf import host
from myf.host import hooks


def test_keen_is_bound_under_its_own_name():
    assert (host.name(), host.title()) == ("keen", "Keen")
    assert host.env("public_base_url") == "KEEN_PUBLIC_BASE_URL"
    assert (
        host.module("config").settings
        is __import__("app.core.config", fromlist=["settings"]).settings
    )


def test_a_binding_cannot_be_changed_once_made():
    with pytest.raises(RuntimeError, match="already bound to app.db.models"):
        host.bind(name="keen", title="Keen", models="other.models")
    with pytest.raises(RuntimeError, match="application name is already 'keen'"):
        host.bind(name="other", title="Keen")
    with pytest.raises(ValueError, match="Unknown myf host roles: storage"):
        host.bind(name="keen", title="Keen", storage="x")
    with pytest.raises(ValueError, match="lower-case identifier"):
        host._identity.clear()
        try:
            host.bind(name="Not-An-Identifier", title="x")
        finally:
            host._identity.update(name="keen", title="Keen")


def test_an_unbound_application_is_named_as_the_problem(monkeypatch):
    monkeypatch.delitem(host._bound, "db")
    with pytest.raises(RuntimeError, match="No application is bound to myf's 'db'"):
        host.module("db")
    monkeypatch.setattr(host, "_identity", {})
    with pytest.raises(RuntimeError, match="call myf.host.bind"):
        host.name()


@pytest.mark.parametrize(
    "name,call,default",
    [
        ("session_allowed", lambda: hooks.session_allowed(None, None, {}), True),
        ("sign_in_methods", hooks.sign_in_methods, {}),
        ("security_notice", lambda: hooks.security_notice("mcp-connected"), None),
        ("origin_required", hooks.origin_required, False),
        ("permission_implications", hooks.permission_implications, {}),
        ("body_limit", lambda: hooks.body_limit("/x"), hooks.DEFAULT_BODY_LIMIT),
        ("encoded_slash_routes", hooks.encoded_slash_routes, None),
        ("measured_fields", hooks.measured_fields, {}),
        ("issued_secret_pattern", hooks.issued_secret_pattern, None),
        ("route_dependencies", hooks.route_dependencies, ()),
        ("route_policy", hooks.route_policy, None),
        ("json_value_limit", lambda: hooks.json_value_limit("/x"), 20_000),
        (
            "json_body_limit",
            lambda: hooks.json_body_limit("/v1/admin/x"),
            __import__("app.security.request_policy", fromlist=["x"]).body_limit(
                "/v1/admin/x"
            ),
        ),
        ("bootstrap_allowed", hooks.bootstrap_allowed, True),
        (
            "inner_request_user",
            lambda: hooks.inner_request_user(None, None),
            hooks.NOT_INNER,
        ),
        ("user_created", lambda: hooks.user_created(None, None), None),
        (
            "sso_identity_allowed",
            lambda: hooks.sso_identity_allowed("oidc", "issuer", "subject"),
            None,
        ),
    ],
)
def test_a_hook_an_application_leaves_out_keeps_myfs_default(
    monkeypatch, name, call, default
):
    monkeypatch.delattr(keen_hooks, name)
    assert call() == default


def test_hooks_without_a_bound_hooks_module_keep_their_defaults(monkeypatch):
    monkeypatch.delitem(host._bound, "hooks")
    assert hooks.session_allowed(None, None, {}) is True
