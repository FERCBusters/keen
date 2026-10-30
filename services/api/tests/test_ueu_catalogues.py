"""Published UEU catalogues: what KEEN holds, as signed-in accounts see it."""

import hashlib
import json

from app.agents.ueu import catalogue as catalogues

from tests.test_assembled_authority_regressions import keen as keen

BUILTIN = catalogues.BUILTIN / "keen-2026.10.json"


def deployment_catalogue(tmp_path, monkeypatch, ident="acme-cloud", version="1"):
    doc = {
        "catalogue": {"id": ident, "version": version, "description": "Acme <b>"},
        "event_types": {
            "acme.thing.observed": {
                "class": "observation",
                "object_type": "thing",
                "subject": "forbidden",
                "payload": "forbidden",
                "facts": {"ready": {"type": "boolean"}},
            }
        },
    }
    raw = json.dumps(doc, indent=1).encode()
    (tmp_path / f"{ident}.json").write_bytes(raw)
    monkeypatch.setenv("KEEN_UEU_CATALOGUE_DIR", str(tmp_path))
    catalogues._load.cache_clear()
    return raw


def test_catalogues_are_listed_and_served_exactly_as_loaded(
    keen, tmp_path, monkeypatch
):
    raw = deployment_catalogue(tmp_path, monkeypatch)
    try:
        client = keen.client(keen.user())
        listing = client.get("/v1/ueu/catalogues")
        assert listing.status_code == 200, listing.text
        items = {(i["id"], i["version"]): i for i in listing.json()["items"]}
        builtin = BUILTIN.read_bytes()
        assert items[("keen", "2026.10")] == {
            "id": "keen",
            "version": "2026.10",
            "description": catalogues.keen().description,
            "origin": "built-in",
            "event_types": len(catalogues.keen().event_types),
            "sha256": hashlib.sha256(builtin).hexdigest(),
            "size_bytes": len(builtin),
        }
        extra = items[("acme-cloud", "1")]
        assert extra["origin"] == "deployment" and extra["event_types"] == 1
        assert extra["sha256"] == hashlib.sha256(raw).hexdigest()

        served = client.get("/v1/ueu/catalogues/keen/2026.10")
        assert served.status_code == 200
        assert served.content == builtin
        assert served.headers["content-type"].startswith("application/json")
        assert "attachment" in served.headers["content-disposition"]
        assert "keen-2026.10.json" in served.headers["content-disposition"]
        assert client.get("/v1/ueu/catalogues/acme-cloud/1").content == raw
    finally:
        monkeypatch.delenv("KEEN_UEU_CATALOGUE_DIR")
        catalogues._load.cache_clear()


def test_catalogues_need_a_session_and_unknown_ones_are_not_found(keen):
    anonymous = keen.client()
    assert anonymous.get("/v1/ueu/catalogues").status_code == 401
    assert anonymous.get("/v1/ueu/catalogues/keen/2026.10").status_code == 401
    client = keen.client(keen.user())
    for path in (
        "/v1/ueu/catalogues/keen/1999",
        "/v1/ueu/catalogues/missing/2026.10",
        "/v1/ueu/catalogues/..%2F..%2Fetc/passwd",
    ):
        assert client.get(path).status_code == 404, path


def test_loaded_catalogues_record_their_origin(tmp_path, monkeypatch):
    raw = deployment_catalogue(tmp_path, monkeypatch, "other", "2")
    try:
        held = catalogues.lookup("other", "2")
        assert held.origin == "deployment" and held.content == raw
        assert catalogues.keen().origin == "built-in"
        # The bytes are kept for publication but never shown in a repr.
        assert "content" not in repr(held)
    finally:
        monkeypatch.delenv("KEEN_UEU_CATALOGUE_DIR")
        catalogues._load.cache_clear()
