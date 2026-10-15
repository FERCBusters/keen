"""Control reference formatting, sorting and catalogue link resolution."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core import references as rf


class Obj(SimpleNamespace):
    pass


def test_control_justification_labels():
    assert rf.control_justification(
        {"metadata": {"justification": "Best Practice"}}
    ) == ("Best Practice")
    assert (
        rf.control_justification(
            {"meta": {"soa_justification": "result of risk assessment"}}
        )
        == "Result of Risk Assessment"
    )
    assert (
        rf.control_justification(
            {"metadata": {"control_justification": "Applicable but not Implemented"}}
        )
        == "Applicable but not Implemented"
    )
    assert (
        rf.control_justification(
            {"metadata": {"applicability_justification": "Limited Applicability"}}
        )
        == "Limited Applicability"
    )
    assert (
        rf.control_justification({"metadata": {"justification": "best_practice"}})
        == "Best Practice"
    )
    assert (
        rf.control_justification(
            {"metadata": {"justification": "applicable_not_implemented"}}
        )
        == "Applicable but not Implemented"
    )
    assert (
        rf.control_justification({"metadata": {"justification": "Made Up Reason"}})
        is None
    )
    assert rf.control_justification({"metadata": {"justification": "  "}}) is None
    assert rf.control_justification({"metadata": {}}) is None
    assert rf.control_justification({"metadata": {"justification": 1}}) is None
    assert rf.control_justification({}) is None
    assert rf.control_justification({"meta": "junk-string"}) is None
    assert rf.control_justification("string-value") is None
    assert (
        rf.control_justification(Obj(meta={"justification": "risk_assessment"}))
        == "Result of Risk Assessment"
    )
    broken = Obj()
    broken.meta = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rf.control_justification(broken) is None


def test_ref_sort_key_natural_order():
    numbers = ["A.8.34", "A.8.10", "A.8.1", "4.1", "8.2.3", "11.8.1.a", "11.11.4.c"]
    # Annex-style refs (alpha bucket) sort before clause numbers.
    ordered = ["A.8.1", "A.8.10", "A.8.34", "4.1", "8.2.3", "11.8.1.a", "11.11.4.c"]
    assert sorted(numbers, key=rf.ref_sort_key) == ordered
    assert rf.ref_sort_key("") == (9, "")
    assert rf.ref_sort_key(None) == (9, "")
    assert rf.ref_sort_key("...") >= (2,)
    assert rf.ref_sort_key("extra-token")[:1] == (2,)
    assert rf.ref_sort_key("12.x") < rf.ref_sort_key("12.y")
    assert rf.ref_sort_key("a.1") < rf.ref_sort_key("z.2")
    keys = [rf.ref_sort_key(r) for r in ["5", "5.1", "5.2", "5.10"]]
    assert keys == sorted(keys)
    assert rf.ref_sort_key("A.5") < rf.ref_sort_key("B.2")
    assert rf.ref_sort_key("a.1") != rf.ref_sort_key("A.2")


def test_try_uuid():
    import uuid

    parsed = rf.try_uuid("7f6cc9e2-4372-4b4b-b8d6-dfa10c001234")
    assert isinstance(parsed, uuid.UUID)
    assert rf.try_uuid("not-a-uuid") is None
    assert rf.try_uuid(12345) is None
    assert rf.try_uuid("") is None


def test_absolute_http_url():
    assert rf.absolute_http_url("https://example.com/a") == "https://example.com/a"
    assert rf.absolute_http_url(" https://example.com ") == "https://example.com"
    assert rf.absolute_http_url("HTTP://Example.com") == "HTTP://Example.com"
    assert rf.absolute_http_url("https://") is None
    assert rf.absolute_http_url("ftp://example.com") is None
    assert rf.absolute_http_url("//example.com/x") is None
    assert rf.absolute_http_url("") is None
    assert rf.absolute_http_url(None) is None
    assert rf.absolute_http_url("https://example.com/\\evil") is None
    assert rf.absolute_http_url("https://example.com/\r\nx") is None
    assert rf.absolute_http_url("https://" + "a" * 3000) is None
    assert rf.absolute_http_url("https://example.com/" + "\\") is None


def test_upstream_url_prefers_metadata(tmp_path):
    control = Obj(meta={"upstream_url": "https://catalogue.example/a"}, ref="A.9.1")
    # Metadata always wins over the YAML catalogue and skips the lookup.
    with patch.object(rf, "load_control_links", return_value={}) as loader:
        assert rf.control_upstream_url(control) == "https://catalogue.example/a"
        loader.assert_not_called()

    from unittest.mock import MagicMock

    broken = MagicMock()
    broken.meta = None
    broken.ref = "A.9.1"
    broken.framework_slug = "ISO-Bucket"
    links = {
        "ISO-Bucket": {"A.9.1": "https://catalogue.example/iso"},
        "__default__": {"A.9.1": "https://catalogue.example/default"},
    }
    with patch.object(rf, "load_control_links", return_value=links):
        assert rf.control_upstream_url(broken) == "https://catalogue.example/iso"

    # With a framework present, unrelated framework buckets are never used.
    with patch.object(
        rf,
        "load_control_links",
        return_value={"OTHER": {"A.9.1": "https://catalogue.example/other"}},
    ):
        assert rf.control_upstream_url(broken) is None

    # Without a framework, any framework bucket provides a last-resort link.
    bare = MagicMock()
    bare.meta = None
    bare.ref = "A.9.1"
    bare.framework = None
    with patch.object(
        rf,
        "load_control_links",
        return_value={"OTHER": {"A.9.1": "https://catalogue.example/other"}},
    ):
        assert rf.control_upstream_url(bare) == "https://catalogue.example/other"
        bare.framework_slug = None
        assert rf.control_upstream_url(bare) == "https://catalogue.example/other"


def test_control_upstream_url_dict_form(tmp_path):
    links = {
        "__default__": {"A.5.1": "https://handbook.example/default"},
        "ISO-27001": {"A.6.2": "https://handbook.example/remote"},
    }
    with patch.object(rf, "load_control_links", return_value=links):
        assert (
            rf.control_upstream_url({"ref": "A.5.1", "framework_slug": None})
            == "https://handbook.example/default"
        )
        assert (
            rf.control_upstream_url({"ref": " A.6.2 ", "framework": " ISO-27001 "})
            == "https://handbook.example/remote"
        )
        # A framework without the ref must not cross into other framework
        # buckets (the default bucket still applies).
        assert rf.control_upstream_url(
            {"ref": "A.5.1", "framework_slug": "ISO-27001"}
        ) == ("https://handbook.example/default")
        assert (
            rf.control_upstream_url({"ref": "9.9", "framework_slug": "ISO-27001"})
            is None
        )


def test_load_control_links_layouts(tmp_path):
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(
        "A.5.1: https://old.example/links/a51\n"
        "A.8.1:\n  url: https://old.example/links/a81\n"
    )
    result = rf.load_control_links(str(legacy))
    assert result["__default__"] == {
        "A.5.1": "https://old.example/links/a51",
        "A.8.1": "https://old.example/links/a81",
    }

    multi = tmp_path / "multi.yml"
    multi.write_text(
        "default:\n  A.5.1: https://new.example/default/a51\n"
        "frameworks:\n"
        "  iso_27001_2022:\n"
        "    A.5.1: https://new.example/iso/a51\n"
        "  'UK-DVSTF:1.0':\n"
        "    '12.5.a': https://new.example/dvstf/12-5-a\n"
    )
    result = rf.load_control_links(str(multi))
    assert result["iso_27001_2022"]["A.5.1"] == "https://new.example/iso/a51"
    assert result["UK-DVSTF:1.0"]["12.5.a"] == "https://new.example/dvstf/12-5-a"
    assert result["__default__"]["A.5.1"] == "https://new.example/default/a51"

    # Provenance note: only the explicit `frameworks:` bucket form produces
    # framework buckets; bare top-level ref/URL maps are treated as default
    # bucket entries because the URL extractor recurses into dict values.
    frameworks_only = tmp_path / "frameworks-only.yml"
    frameworks_only.write_text(
        "frameworks:\n"
        "  iso_27001_2022:\n"
        "    A.5.1: https://short.example/a51\n"
        "    A.6.2: https://short.example/a62\n"
    )
    result = rf.load_control_links(str(frameworks_only))
    assert result["iso_27001_2022"]["A.5.1"] == "https://short.example/a51"
    assert result["iso_27001_2022"]["A.6.2"] == "https://short.example/a62"

    listform = tmp_path / "listform.yml"
    listform.write_text(
        "controls:\n"
        "  - ref: A.5.1\n"
        "    href: https://obj.example/a51\n"
        "  - ref: A.6.1\n"
        "    link: https://obj.example/a61\n"
    )
    result = rf.load_control_links(str(listform))
    # A `controls:` alias holding a list lands in the default bucket.
    assert result["__default__"]["A.5.1"] == "https://obj.example/a51"
    assert result["__default__"]["A.6.1"] == "https://obj.example/a61"

    # The controls alias and single-key dict forms resolve nested URLs too.
    alias = tmp_path / "alias.yml"
    alias.write_text(
        "controls:\n  A.5.1:\n    upstream_url: https://alias.example/a51\n"
    )
    result = rf.load_control_links(str(alias))
    assert result["__default__"]["A.5.1"] == "https://alias.example/a51"

    single = tmp_path / "single.yml"
    single.write_text("A.5.1:\n  https://only.example/a51\n")
    result = rf.load_control_links(str(single))
    assert result["__default__"]["A.5.1"] == "https://only.example/a51"

    missing = rf.load_control_links(str(tmp_path / "missing.yml"))
    assert missing == {"__default__": {}}

    fallback = tmp_path / "fallback.yml"
    fallback.write_text("A.5.1: https://fallback.example/a51\n")
    with patch("yaml.safe_load", side_effect=RuntimeError("bad yaml")):
        result_ = rf.load_control_links(str(fallback))
    assert result_ == {"__default__": {"A.5.1": "https://fallback.example/a51"}}

    empty = tmp_path / "empty.yml"
    empty.write_text("")
    assert rf.load_control_links(str(empty)) == {"__default__": {}}

    junk = tmp_path / "junk.yml"
    junk.write_text("- just\n- a\n- list")
    assert rf.load_control_links(str(junk))["__default__"] == {}

    scalar = tmp_path / "scalar.yml"
    scalar.write_text("42\n")
    assert rf.load_control_links(str(scalar)) == {"__default__": {}}

    www = tmp_path / "www.yml"
    www.write_text("A.5.1: www.example.com/docs\n")
    assert rf.load_control_links(str(www))["__default__"]["A.5.1"] == (
        "https://www.example.com/docs"
    )

    embedded = tmp_path / "embedded.yml"
    embedded.write_text("A.5.1: 'see also https://link.example/x (docs)'\n")
    assert rf.load_control_links(str(embedded))["__default__"]["A.5.1"] == (
        "https://link.example/x (docs)"
    )

    unreachable = rf.load_control_links("/unreachable/nonexistent.yml")
    assert unreachable == {"__default__": {}}
