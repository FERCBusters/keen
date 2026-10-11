import pytest
from app.core import source_meta


@pytest.mark.parametrize(
    "source,label",
    [
        ("rss", "RSS"),
        ("github", "GitHub"),
        ("gitlab", "GitLab"),
        ("bookstack", "BookStack"),
        ("cloudwatch_logs", "AWS CloudWatch Logs"),
    ],
)
def test_canonical_source_labels_preserve_machine_keys(monkeypatch, source, label):
    monkeypatch.setattr(source_meta, "_configured_source_meta", lambda _: {})
    monkeypatch.delenv("KEEN_SOURCE_NAME_" + source.upper(), raising=False)
    meta = source_meta.get_source_meta(source)
    assert meta.source == source
    assert meta.label == label


def test_explicit_source_label_wins(monkeypatch):
    monkeypatch.setattr(
        source_meta, "_configured_source_meta", lambda _: {"rss": {"label": "My feed"}}
    )
    monkeypatch.delenv("KEEN_SOURCE_NAME_RSS", raising=False)
    assert source_meta.get_source_meta("rss").label == "My feed"
    monkeypatch.setenv("KEEN_SOURCE_NAME_RSS", "Bulletins")
    assert source_meta.get_source_meta("rss").label == "Bulletins"
