"""Plugin registry behaviour with synthetic plugin callables."""

from unittest.mock import MagicMock, patch

import pytest

from app.plugins import registry


@pytest.mark.parametrize(
    "document,expected",
    [
        ({}, []),
        ({"plugins": None}, []),
        ({"plugins": "not-a-list"}, []),
        ({"plugins": ["junk", {"name": "x"}, {"callable": "m:f"}]}, []),
        (
            {
                "plugins": [
                    {"name": "p1", "callable": "mod:fn"},
                    {
                        "name": "p2",
                        "callable": "mod:fn",
                        "enabled_setting": "p2_enabled",
                    },
                ]
            },
            ["p1", "p2"],
        ),
    ],
)
def test_load_plugins(tmp_path, document, expected):
    path = tmp_path / "plugins.yml"
    path.write_text("")
    with patch.object(registry, "_load_yaml", return_value=document):
        specs = registry.load_plugins(str(path))
    assert [s.name for s in specs] == expected
    if expected:
        assert specs[1].enabled_setting == "p2_enabled"


def test_load_yaml(tmp_path):
    path = tmp_path / "plugins.yml"
    path.write_text("plugins:\n  - name: p1\n")
    assert registry._load_yaml(str(path)) == {"plugins": [{"name": "p1"}]}


def test_resolve_callable():
    assert callable(registry.resolve_callable("app.plugins.registry:resolve_callable"))
    with pytest.raises(ValueError):
        registry.resolve_callable("no colon here")
    with pytest.raises(AttributeError):
        registry.resolve_callable("app.plugins.registry:not_a_real_function")


def test_run_plugins_orders_skips_and_errors():
    db = MagicMock()

    def failing_plugin(_database):
        raise RuntimeError("plugin crashed")

    with (
        patch.object(
            registry,
            "load_plugins",
            return_value=[
                registry.PluginSpec("enabled", "app.plugins.registry:resolve_callable"),
                registry.PluginSpec(
                    "disabled", "app.plugins.registry:resolve_callable", "demo_mode"
                ),
                registry.PluginSpec("crashing", "app.plugins.registry:crash"),
            ],
        ),
        patch.object(registry.settings, "demo_mode", False),
    ):
        with patch.object(
            registry,
            "resolve_callable",
            side_effect=[lambda db: {"ran": True}, RuntimeError("boom")],
        ):
            results = registry.run_plugins(db, config_path="plugins.yml")
    assert results[0] == {"plugin": "enabled", "result": {"ran": True}}
    assert results[1] == {
        "plugin": "disabled",
        "skipped": True,
        "reason": "demo_mode=false",
    }
    assert results[2]["plugin"] == "crashing" and "boom" in results[2]["error"]
    db.rollback.assert_called_once()
