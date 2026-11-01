"""Source display metadata: config files, env overrides and generated defaults."""

from unittest.mock import MagicMock, patch

import pytest
from app.core import managed_configuration as mc
from app.core import source_meta as sm


def test_normalize_hex_color():
    assert sm.normalize_hex_color("#ff00aa") == "#FF00AA"
    assert sm.normalize_hex_color("ff00aa") == "#FF00AA"
    assert sm.normalize_hex_color("#F0A") == "#FF00AA"
    assert sm.normalize_hex_color("F0A") == "#FF00AA"
    assert sm.normalize_hex_color(" #00ff00 ") == "#00FF00"
    assert sm.normalize_hex_color("") is None
    assert sm.normalize_hex_color(None) is None
    assert sm.normalize_hex_color("12g456") is None
    assert sm.normalize_hex_color("#12345") is None
    assert sm.normalize_hex_color("#1234567") is None


def test_humanize_source():
    assert sm._humanize_source("cloudwatch_logs") == "AWS CloudWatch Logs"
    assert sm._humanize_source("gecko") == "Gecko"
    assert sm._humanize_source("my_audit_tool") == "My audit tool"
    assert sm._humanize_source("webhook:ops-slack") == "Webhook: Ops slack"
    assert sm._humanize_source("service-a:b_c") == "Service a: B c"
    assert sm._humanize_source("") == ""


def test_hsl_to_rgb_segments_and_bounds():
    # Each 60-degree hue band maps to a distinct primary mix.
    assert sm._hsl_to_rgb(0, 1, 0.5) == (255, 0, 0)
    assert sm._hsl_to_rgb(60, 1, 0.5) == (255, 255, 0)
    assert sm._hsl_to_rgb(120, 1, 0.5) == (0, 255, 0)
    assert sm._hsl_to_rgb(180, 1, 0.5) == (0, 255, 255)
    assert sm._hsl_to_rgb(240, 1, 0.5) == (0, 0, 255)
    assert sm._hsl_to_rgb(300, 1, 0.5) == (255, 0, 255)
    # Out-of-band hues clamp into the standard 0-360 domain without error.
    for value in (-1.0, 400.0, 60.0, 300.0):
        r, g, b = sm._hsl_to_rgb(value, 0.5, 0.5)
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255


def test_auto_color_deterministic_hex():
    first = sm._auto_color("fresh-source")
    assert first == sm._auto_color("fresh-source")
    assert first.startswith("#") and len(first) == 7


def test_env_key_sanitisation():
    assert sm._env_key("cloudwatch_logs") == "CLOUDWATCH_LOGS"
    assert sm._env_key("webhook:some-prov.1") == "WEBHOOK_SOME_PROV_1"


def test_merge_only_with_values():
    meta: dict = {}
    sm._merge(meta, "taiga", label="Tracker", color="#FF00AA")
    assert meta["taiga"] == {"label": "Tracker", "color": "#FF00AA"}
    meta2: dict = {}
    sm._merge(meta2, "x", label=None, color=None)
    assert meta2 == {}


def documents_of(load_document):
    """A stand-in for load_documents built from a one-document loader."""
    return lambda paths: {
        name: load_document(name, path) for name, path in paths.items()
    }


def test_configured_source_meta_reads_catalogue(monkeypatch):
    sm._configured_source_meta.cache_clear()
    documents = {
        "taiga": {"ui_name": "Tracker", "ui_color": "#00aa00"},
        "gitea": {"display_name": " Git Host ", "badge_color": "0af"},
        "bookstack": {"ui_color": "not-a-color"},
        "webhooks": {
            "providers": {
                "alarm-monitor": {"ui_name": "Alarm Monitor", "badge_color": "#101010"},
                "bad": "junk",
            }
        },
    }

    def load_document(src, path):
        return documents.get(src, {})

    collector = MagicMock()
    collector.id = "custom-1"
    collector.name = "CSV importer"
    fake_db = MagicMock()
    fake_db.query.return_value.all.return_value = [collector]
    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_db
    with (
        patch.object(sm, "load_documents", documents_of(load_document)),
        patch("app.db.session.SessionLocal", lambda: fake_session),
    ):
        meta = sm._configured_source_meta(42)
    assert meta["taiga"] == {"label": "Tracker", "color": "#00AA00"}
    assert meta["gitea"]["label"] == "Git Host"
    assert meta["gitea"]["color"] == "#00AAFF"
    assert "bookstack" not in meta
    assert meta["webhook:alarm-monitor"]["color"] == "#101010"
    assert meta["integration:custom-1"] == {
        "label": "CSV importer",
        "color": "#6935BE",
    }
    assert meta["keen-agent"] == {"label": "Keen Agent", "color": "#6935BE"}


def test_documents_are_read_in_one_session_with_shipped_fallbacks(tmp_path):
    shipped = tmp_path / "rss.yml"
    shipped.write_text("ui_name: Feeds\n")
    stored = MagicMock()
    stored.execute.return_value = [("taiga", {"ui_name": "Tracker"})]
    session = MagicMock()
    session.__enter__.return_value = stored
    opened = []
    with patch.object(mc, "SessionLocal", lambda: opened.append(1) or session):
        documents = mc.load_documents(
            {"taiga": str(tmp_path / "absent.yml"), "rss": str(shipped)}
        )
    assert documents == {"taiga": {"ui_name": "Tracker"}, "rss": {"ui_name": "Feeds"}}
    assert opened == [1] and stored.execute.call_count == 1
    shipped.write_text("- not\n- an object\n")
    with patch.object(mc, "SessionLocal", lambda: session):
        stored.execute.return_value = []
        with pytest.raises(ValueError, match="rss configuration must be an object"):
            mc.load_documents({"rss": str(shipped)})


def test_configured_source_meta_survives_database_outage(monkeypatch):
    sm._configured_source_meta.cache_clear()
    documents: dict = {}

    def load_document(src, path):
        return documents.setdefault((src, path), {})

    broken_session = MagicMock()
    broken_session.__enter__ = MagicMock(side_effect=RuntimeError("db is down"))
    with (
        patch.object(sm, "load_documents", documents_of(load_document)),
        patch("app.db.session.SessionLocal", lambda: broken_session),
    ):
        meta = sm._configured_source_meta(42)
        meta = sm._configured_source_meta(2)
    assert meta["keen-agent"]["color"] == "#6935BE"


def test_get_source_meta_priority(monkeypatch):
    cached = {"taiga": {"label": "Tracker", "color": "#00AA00"}}
    with patch.object(sm, "_configured_source_meta", return_value=cached):
        with monkeypatch.context() as ctx:
            meta = sm.get_source_meta("taiga")
            assert (meta.source, meta.label, meta.color) == (
                "taiga",
                "Tracker",
                "#00AA00",
            )

            ctx.setenv("KEEN_SOURCE_NAME_TAIGA", " Tickets ")
            ctx.setenv("KEEN_SOURCE_COLOR_TAIGA", "#0f0f0f")
            meta = sm.get_source_meta("taiga")
            assert meta.label == "Tickets" and meta.color == "#0F0F0F"
            ctx.delenv("KEEN_SOURCE_NAME_TAIGA")
            ctx.delenv("KEEN_SOURCE_COLOR_TAIGA")

            # Unknown sources generate their own label and deterministic colour.
            generated = sm.get_source_meta("custom_tool")
            assert generated.label == "Custom tool"
            assert generated.color == sm._auto_color("custom_tool")

            # Blank sources fall back to "unknown".
            assert sm.get_source_meta("   ").source == "unknown"

            # Invalid colour overrides keep the generated colour.
            ctx.setenv("KEEN_SOURCE_COLOR_CUSTOM_TOOL", "zzz")
            assert sm.get_source_meta("custom_tool").color == sm._auto_color(
                "custom_tool"
            )


def test_apply_user_source_overrides():
    meta = sm.SourceMeta(source="taiga", label="Tracker", color="#FF00AA")
    override = sm.apply_user_source_overrides(meta, {"taiga": "#123456"})
    assert override == sm.SourceMeta("taiga", "Tracker", "#123456")
    assert sm.apply_user_source_overrides(meta, {"other": "#123456"}) is meta
    assert sm.apply_user_source_overrides(meta, {"taiga": "pink"}) is meta
    assert sm.apply_user_source_overrides(meta, None) is meta
    assert sm.apply_user_source_overrides(meta, []) is meta


def test_environment_overrides_apply_to_configured_sources_at_load(monkeypatch):
    # KEEN_SOURCE_NAME_* and KEEN_SOURCE_COLOR_* win over catalogue values for
    # configured sources; blank names, unusable colours and keys without a
    # source name are ignored, so the metadata stays usable.
    sm._configured_source_meta.cache_clear()
    documents = {
        "taiga": {"ui_name": "Tracker", "ui_color": "#00aa00"},
        "jenkins": {"ui_name": "CI"},
        # A providers section that is no mapping contributes nothing.
        "webhooks": {"providers": ["alarm-monitor"]},
    }
    broken_session = MagicMock()
    broken_session.__enter__ = MagicMock(side_effect=RuntimeError("db is down"))
    monkeypatch.setenv("KEEN_SOURCE_NAME_TAIGA", " Tickets ")
    monkeypatch.setenv("KEEN_SOURCE_COLOR_TAIGA", "0f0f0f")
    monkeypatch.setenv("KEEN_SOURCE_NAME_JENKINS", "   ")
    monkeypatch.setenv("KEEN_SOURCE_COLOR_JENKINS", "not-a-colour")
    monkeypatch.setenv("KEEN_SOURCE_NAME_", "nameless")
    monkeypatch.setenv("KEEN_SOURCE_COLOR_", "#123456")
    monkeypatch.setenv("KEEN_SOURCE_NAME_DIARY", "Journal")
    with (
        patch.object(
            sm,
            "load_documents",
            documents_of(lambda src, path: documents.get(src, {})),
        ),
        patch("app.db.session.SessionLocal", lambda: broken_session),
    ):
        meta = sm._configured_source_meta(7)
    assert meta["taiga"] == {"label": "Tickets", "color": "#0F0F0F"}
    assert meta["jenkins"] == {"label": "CI"}
    # Sources that only exist in the environment are resolved at lookup time.
    assert "diary" not in meta and "" not in meta
    assert not [key for key in meta if key.startswith("webhook:")]
    with patch.object(sm, "_configured_source_meta", return_value=meta):
        assert sm.get_source_meta("diary").label == "Journal"
