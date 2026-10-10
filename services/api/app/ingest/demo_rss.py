"""The single public feed permitted in hosted demos; no credentials or user URLs."""

from copy import deepcopy

FEED = {
    "name": "mig5 supplier news (live demo)",
    "label": "mig5 supplier news",
    "system": "mig5 supplier news",
    "url": "https://mig5.net/news/index.xml",
    "enabled": True,
    "max_items": 20,
    "overlap_minutes": 2880,
}
RULE = {
    "id": "demo_mig5_supplier_news",
    "description": "Public supplier communications collected for review. Collection alone does not establish compliance or completion of a supplier review.",
    "when": {"source": "rss", "system": FEED["system"]},
    "map_to": {"iso_27001_2022": ["A.5.19", "A.5.22"], "KEEN-AF:1.0": ["TPM-2"]},
    "confidence": 0.6,
}


def seed_preset(db):
    from app.core.config import settings
    from app.core.managed_configuration import load_document
    from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
    from app.mapping.rules import _as_rule_entries

    if not settings.demo_mode:
        return
    raw = load_document("rules", settings.rules_path, db=db)
    rules = []
    for inherited, entry in _as_rule_entries(raw):
        item = deepcopy(entry)
        if inherited and "framework" not in item and "frameworks" not in item:
            item["framework"] = inherited
        rules.append(item)
    documents = {"rss": {"feeds": [deepcopy(FEED)]}}
    if not any(r.get("id") == RULE["id"] for r in rules):
        documents["rules"] = {"rules": rules + [deepcopy(RULE)]}
    for name, document in documents.items():
        row = db.get(ManagedConfiguration, name)
        if row is not None and row.document == document:
            continue
        if row is None:
            row = ManagedConfiguration(name=name, document=document, version=1)
            db.add(row)
        else:
            row.document = document
            row.version += 1
        db.add(
            ManagedConfigurationRevision(
                name=name, version=row.version, document=deepcopy(document)
            )
        )
    db.flush()
