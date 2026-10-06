from types import SimpleNamespace
from unittest.mock import Mock
from app.ingest import rss
from app.ingest.demo_rss import FEED, RULE
from app.mapping.rules import parse_rules, evaluate_by_framework


def test_demo_rejects_other_feeds(monkeypatch):
    monkeypatch.setattr(rss.settings, 'demo_mode', True)
    assert rss.ingest_rss_feed(Mock(), {'url':'https://other.example/feed'})['ok'] is False


def test_demo_uses_only_preset(monkeypatch):
    monkeypatch.setattr(rss.settings, 'demo_mode', True)
    monkeypatch.setattr(rss.settings, 'rss_enabled', True)
    fn = Mock(return_value={'ok':True})
    monkeypatch.setattr(rss, 'ingest_rss_feed', fn)
    monkeypatch.setattr(rss, 'load_rss_config', lambda *a: (_ for _ in ()).throw(AssertionError('Demo must ignore arbitrary config')))
    db = Mock()
    rss.ingest_rss_all(db)
    fn.assert_called_once_with(db, FEED)


def test_supplier_rule_matches_only_preset_source():
    rules = parse_rules({'rules':[RULE]})
    hits = evaluate_by_framework({'source':'rss','system':FEED['system']}, rules)
    assert set(hits['ISO27001:2022']) == {'A.5.19','A.5.22'}
    assert hits['KEEN-AF:1.0'] == ['TPM-2']
    assert not evaluate_by_framework({'source':'rss','system':'Another feed'},rules)
