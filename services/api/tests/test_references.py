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


def test_a_rows_own_catalogue_link_comes_first():
    control = Obj(
        meta={"upstream_url": "https://catalogue.example/a"},
        ref="A.9.1",
        framework_slug="iso",
    )
    with patch.object(rf, "load_control_links") as loader:
        assert rf.control_upstream_url(control) == "https://catalogue.example/a"
        loader.assert_not_called()

    # Imported metadata counts only as an absolute web link.
    links = {"iso": {"A.9.1": "https://links.example/iso"}}
    with patch.object(rf, "load_control_links", return_value=links):
        for value in ("javascript:alert(1)", "/relative", 42, None):
            control.meta = {"upstream_url": value}
            assert rf.control_upstream_url(control) == "https://links.example/iso"
        control.meta = None
        assert rf.control_upstream_url(control) == "https://links.example/iso"


def test_the_links_file_applies_only_under_the_rows_framework(monkeypatch):
    monkeypatch.setattr(rf.settings, "control_links_path", "/links/control_links.yml")
    links = {
        "iso": {"5.1": "https://links.example/iso/5-1"},
        "dvstf": {"5.1": "https://links.example/dvstf/5-1"},
    }
    with patch.object(rf, "load_control_links", return_value=links) as loader:
        iso = Obj(meta={}, ref=" 5.1 ", framework_slug=" iso ")
        assert rf.control_upstream_url(iso) == "https://links.example/iso/5-1"
        loader.assert_called_with("/links/control_links.yml")
        dvstf = Obj(meta={}, ref="5.1", framework_slug="dvstf")
        assert rf.control_upstream_url(dvstf) == "https://links.example/dvstf/5-1"
        # Clause references such as 5.1 recur across frameworks, so another
        # framework's link never stands in.
        other = Obj(meta={}, ref="5.1", framework_slug="nist")
        assert rf.control_upstream_url(other) is None
        for row in (
            Obj(meta={}, ref="5.1", framework_slug=None),
            Obj(meta={}, ref="5.1", framework_slug=""),
            Obj(meta={}, ref="", framework_slug="iso"),
            Obj(meta={}, ref=None, framework_slug="iso"),
        ):
            assert rf.control_upstream_url(row) is None


def links_file(tmp_path, text):
    path = tmp_path / "control_links.yml"
    path.write_text(text)
    rf.load_control_links.cache_clear()
    return str(path)


def test_control_links_name_each_frameworks_references(tmp_path, caplog):
    path = links_file(
        tmp_path,
        "frameworks:\n"
        "  iso_27001_2022:\n"
        "    A.5.1: https://links.example/iso/a51\n"
        "    ' A.6.2 ': ' https://links.example/iso/a62 '\n"
        "  'UK-DVSTF:1.0':\n"
        "    '5.1': http://links.example/dvstf/5-1\n",
    )

    assert rf.load_control_links(path) == {
        "iso_27001_2022": {
            "A.5.1": "https://links.example/iso/a51",
            "A.6.2": "https://links.example/iso/a62",
        },
        "UK-DVSTF:1.0": {"5.1": "http://links.example/dvstf/5-1"},
    }
    assert not caplog.records
    # The file is read once for the process.
    (tmp_path / "control_links.yml").write_text("frameworks: {}\n")
    assert "iso_27001_2022" in rf.load_control_links(path)


def test_unreadable_link_entries_are_skipped_and_logged(tmp_path, caplog):
    long_ref = "A" * 65
    path = links_file(
        tmp_path,
        "default:\n"
        "  A.5.1: https://links.example/default\n"
        "frameworks:\n"
        "  iso:\n"
        "    A.5.1: https://links.example/iso/a51\n"
        "    5.1: https://links.example/iso/5-1\n"
        f"    {long_ref}: https://links.example/iso/long\n"
        "    A.5.2: javascript:alert(1)\n"
        "    A.5.3: /controls.html\n"
        "    A.5.4: see https://links.example/iso/a54\n"
        "    A.5.5: www.links.example/a55\n"
        "    A.5.6:\n"
        "      url: https://links.example/iso/a56\n"
        "    A.5.7: https://links.example/iso/a57\n"
        "  'bad framework':\n"
        "    A.5.1: https://links.example/bad\n"
        "  listed:\n"
        "    - https://links.example/listed\n",
    )

    with caplog.at_level("WARNING", logger="app.core.references"):
        loaded = rf.load_control_links(path)

    # The readable entries still apply.
    assert loaded == {
        "iso": {
            "A.5.1": "https://links.example/iso/a51",
            "A.5.7": "https://links.example/iso/a57",
        }
    }
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 10
    assert all(message.startswith("Skipping control link ") for message in messages)
    assert all(f" in {path}: " in message for message in messages)
    expected = {
        "'default'": "only frameworks is read",
        "iso 5.1": "not a control or clause reference",
        f"iso '{long_ref}'": "not a control or clause reference",
        "'bad framework'": "not a framework identifier",
        "listed": "needs a map of references to links",
    }
    for ref in ("A.5.2", "A.5.3", "A.5.4", "A.5.5", "A.5.6"):
        expected[f"iso '{ref}'"] = "needs an absolute http or https link"
    assert sorted(messages) == sorted(
        f"Skipping control link {entry} in {path}: {problem}"
        for entry, problem in expected.items()
    )
    # Logged entries name the reference, never the link text.
    assert "javascript" not in " ".join(messages)


@pytest.mark.parametrize(
    "text",
    [
        "A.5.1: https://links.example/a51\n",
        "controls:\n  A.5.1: https://links.example/a51\n",
        "- ref: A.5.1\n  href: https://links.example/a51\n",
        "frameworks:\n  - https://links.example/a51\n",
        "42\n",
    ],
    ids=["references-only", "controls", "list", "frameworks-list", "scalar"],
)
def test_a_file_without_a_frameworks_map_gives_no_links(tmp_path, caplog, text):
    path = links_file(tmp_path, text)

    assert rf.load_control_links(path) == {}
    assert [record.getMessage() for record in caplog.records] == [
        f"Ignoring control links file {path}: it needs a frameworks map"
    ]


def test_a_missing_empty_or_broken_file_gives_no_links(tmp_path, caplog):
    rf.load_control_links.cache_clear()
    assert rf.load_control_links(str(tmp_path / "missing.yml")) == {}
    for empty in ("", "{}\n", "frameworks: {}\n"):
        assert rf.load_control_links(links_file(tmp_path, empty)) == {}
    assert not caplog.records

    broken = links_file(
        tmp_path, "frameworks:\n  iso:\n    A.8.1: A.8.1: https://links.example/a81\n"
    )
    assert rf.load_control_links(broken) == {}
    (message,) = [record.getMessage() for record in caplog.records]
    assert message.startswith(f"Ignoring control links file {broken}: ")
    assert "line 3" in message


def test_the_shipped_links_file_loads_every_entry(caplog):
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "config" / "control_links.yml"
    rf.load_control_links.cache_clear()
    with caplog.at_level("WARNING", logger="app.core.references"):
        loaded = rf.load_control_links(str(path))

    assert not caplog.records
    # One entry per link line: a repeated reference would silently replace
    # another framework item's link.
    lines = [
        line
        for line in path.read_text().splitlines()
        if line.startswith("    ") and not line.lstrip().startswith("#")
    ]
    assert sum(len(refs) for refs in loaded.values()) == len(lines)
    assert {"iso_27001_2022", "UK-DVSTF:1.0"} <= set(loaded)
