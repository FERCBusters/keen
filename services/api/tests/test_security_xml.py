"""The XML boundary for documents returned by evidence sources.

Ordinary feeds parse exactly as the standard library parses them. A document
type declaration with an internal subset, where entities and default attribute
values are declared, is refused before a tree is built, whatever the document's
encoding. The entity and attribute-default documents stay small here; at the
collection response limit, each occupies gigabytes once the standard parser
builds its tree. Application code reaches XML parsers only through the boundary.
"""

import ast
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from app.security.xml import MAX_NAMESPACE_NAME, parse_untrusted_xml

APP = Path(__file__).resolve().parents[1] / "app"
REFUSED = "XML documents with an internal DTD subset are refused"

ATOM = (
    '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
    '<entry><title>Release &amp; notes</title><link href="https://x.example/1"/>'
    "<updated>2026-11-03T00:00:00Z</updated></entry></feed>"
)


def _shape(element):
    return (
        element.tag,
        element.attrib,
        element.text,
        element.tail,
        [_shape(child) for child in element],
    )


@pytest.mark.parametrize(
    "document",
    [
        ATOM,
        ATOM.encode(),
        ATOM.encode("utf-16"),
        '<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel><title>'
        "Caf\xe9</title></channel></rss>".encode("latin-1"),
        # RSS 0.91's public document type names an external DTD only.
        '<?xml version="1.0"?><!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD'
        ' RSS 0.91//EN" "http://my.netscape.com/publish/formats/rss-0.91.dtd">'
        '<rss version="0.91"><channel><title>Old feed</title></channel></rss>',
        "<!-- generated --><?xml-stylesheet href='feed.xsl'?><rss/>",
    ],
    ids=["str", "bytes", "utf-16", "latin-1", "external-dtd", "prolog-markup"],
)
def test_ordinary_documents_parse_as_the_standard_parser_does(document):
    assert _shape(parse_untrusted_xml(document)) == _shape(ET.fromstring(document))


def test_an_external_dtd_is_never_read(tmp_path):
    dtd = tmp_path / "feed.dtd"
    dtd.write_text('<!ENTITY leak "contents of a local file">')
    document = (
        f'<?xml version="1.0"?><!DOCTYPE rss SYSTEM "{dtd.as_uri()}">'
        "<rss><channel><title>&leak;</title></channel></rss>"
    )

    with pytest.raises(ET.ParseError, match="undefined entity"):
        parse_untrusted_xml(document)


@pytest.mark.parametrize(
    "document",
    [
        # Each reference stays inside expat's hundredfold amplification limit,
        # so the standard parser expands all of them.
        '<!DOCTYPE rss [<!ENTITY e "' + "x" * 280 + '">]>'
        "<rss><channel><title>" + "&e;" * 50 + "</title></channel></rss>",
        # Default attribute values fall outside expat's amplification limit.
        '<!DOCTYPE rss [<!ATTLIST a x CDATA "' + "y" * 1000 + '">]>'
        "<rss><channel>" + "<a/>" * 50 + "</channel></rss>",
        "<!DOCTYPE rss [<!ENTITY % p \"<!ENTITY e 'x'>\"> %p;]><rss>&e;</rss>",
        '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>',
        "<!DOCTYPE rss []><rss/>",
        # Another encoding carries the same declaration.
        (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE rss [<!ENTITY e "x">]><rss>&e;</rss>'
        ).encode("utf-16"),
    ],
    ids=[
        "entity",
        "attribute-default",
        "parameter-entity",
        "external-entity",
        "empty-subset",
        "utf-16",
    ],
)
def test_internal_subsets_are_refused_before_a_tree_is_built(document):
    with pytest.raises(ET.ParseError, match=REFUSED):
        parse_untrusted_xml(document)


LONGEST = "u" * MAX_NAMESPACE_NAME


@pytest.mark.parametrize(
    "document",
    [
        f'<rss xmlns:p="{LONGEST}x"><p:a/></rss>',
        f'<rss xmlns="{LONGEST}x"><a/></rss>',
        # A namespace can be declared on any element, however deep.
        f'<rss><channel><item><a xmlns:p="{LONGEST}x"/></item></channel></rss>',
        # Character references count as the characters they stand for.
        '<rss xmlns:p="' + "&#x75;" * (MAX_NAMESPACE_NAME + 1) + '"/>',
        f'<rss xmlns:p="{LONGEST}x"/>'.encode("utf-16"),
        # The bound is the stored width, not the count of characters: one more
        # four-byte or three-byte character than fits is refused.
        '<rss xmlns:p="' + "\U0001f600" * (MAX_NAMESPACE_NAME // 4 + 1) + '"/>',
        '<rss xmlns="' + "\u4e00" * (MAX_NAMESPACE_NAME // 3 + 1) + '"><a/></rss>',
        '<rss xmlns:p="' + "&#x1F600;" * (MAX_NAMESPACE_NAME // 4 + 1) + '"/>',
        # One wide character widens every character of the stored name: ASCII
        # letters and one four-byte character within the bound as UTF-8 are
        # four times the bound in the tree. Latin-1 letters are two bytes each
        # as UTF-8.
        '<rss xmlns:p="' + "u" * (MAX_NAMESPACE_NAME - 4) + "\U0001f600" + '"/>',
        '<rss xmlns:p="' + "u" * (MAX_NAMESPACE_NAME - 3) + "\u4e00" + '"/>',
        '<rss xmlns:p="' + "\xff" * (MAX_NAMESPACE_NAME // 2 + 1) + '"/>',
    ],
    ids=[
        "prefixed",
        "default",
        "nested",
        "references",
        "utf-16",
        "four-byte",
        "three-byte",
        "four-byte references",
        "ascii with one four-byte",
        "ascii with one two-byte-wide",
        "latin-1",
    ],
)
def test_a_long_namespace_name_is_refused_before_a_tree_is_built(document):
    # The tree keeps the namespace name in every distinct qualified name, so
    # one long name on many short names costs their product in memory.
    with pytest.raises(ET.ParseError, match="^XML namespace names over 96 bytes"):
        parse_untrusted_xml(document)


@pytest.mark.parametrize(
    "document",
    [
        f'<rss xmlns:p="{LONGEST}"><p:a p:b="c"/></rss>',
        f'<rss xmlns="{LONGEST}"><a/></rss>',
        # Only a declaration is bounded: other attributes hold any text.
        f'<rss note="{LONGEST}x" xmlnsish="{LONGEST}x"/>',
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        ' xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
        ' xmlns="http://purl.org/rss/1.0/"><channel><itunes:author>a'
        "</itunes:author></channel></rdf:RDF>",
    ],
    ids=["longest-prefixed", "longest-default", "other-attributes", "feed-modules"],
)
def test_namespace_names_within_the_bound_parse_as_the_standard_parser_does(document):
    assert _shape(parse_untrusted_xml(document)) == _shape(ET.fromstring(document))


@pytest.mark.parametrize(
    "document",
    ["", b"not xml", '<?xml version="1.0"?>', "<!DOCTYPE rss", "<rss>"],
)
def test_malformed_documents_raise_the_standard_parse_error(document):
    with pytest.raises(ET.ParseError) as expected:
        ET.fromstring(document)
    with pytest.raises(ET.ParseError) as raised:
        parse_untrusted_xml(document)

    error = raised.value
    assert (str(error), error.code, error.position) == (
        str(expected.value),
        expected.value.code,
        expected.value.position,
    )


def xml_parser_uses(root):
    """List parser calls and parser imports in modules under root."""
    tree_parsers = {
        "XML",
        "XMLID",
        "XMLParser",
        "XMLPullParser",
        "fromstring",
        "fromstringlist",
        "iterparse",
        "parse",
    }
    other_parsers = ("defusedxml", "lxml", "xml.dom", "xml.parsers", "xml.sax")
    uses = []
    for path in sorted(root.rglob("*.py")):
        if path == root / "security/xml.py":
            continue
        where = path.relative_to(root)
        nodes = list(ast.walk(ast.parse(path.read_text())))
        trees = {"xml.etree.ElementTree"}
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "xml.etree.ElementTree" and alias.asname:
                        trees.add(alias.asname)
                    if alias.name.startswith(other_parsers):
                        uses.append(f"{where}:{node.lineno}: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "xml.etree":
                    trees.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == "ElementTree"
                    )
                if module.startswith(other_parsers) or (
                    module == "xml.etree.ElementTree"
                    and any(alias.name in tree_parsers for alias in node.names)
                ):
                    uses.append(f"{where}:{node.lineno}: {module}")
        uses.extend(
            f"{where}:{node.lineno}: {ast.unparse(node)}"
            for node in nodes
            if isinstance(node, ast.Attribute)
            and node.attr in tree_parsers
            and ast.unparse(node.value) in trees
        )
    return uses


def test_application_code_parses_xml_only_through_the_boundary():
    assert xml_parser_uses(APP) == []


def test_the_boundary_check_finds_direct_parser_use(tmp_path):
    (tmp_path / "security").mkdir()
    (tmp_path / "security/xml.py").write_text("import xml.parsers.expat\n")
    (tmp_path / "feeds.py").write_text(
        "import xml.etree.ElementTree\n"
        "import xml.etree.ElementTree as Tree\n"
        "from xml.etree import ElementTree as ET\n"
        "from xml.etree.ElementTree import Element, fromstring\n"
        "from xml.dom import minidom\n"
        "import lxml.etree\n"
        "def read(data: bytes) -> ET.Element:\n"
        "    xml.etree.ElementTree.parse(data)\n"
        "    Tree.iterparse(data)\n"
        "    return ET.fromstring(data)\n"
    )

    assert xml_parser_uses(tmp_path) == [
        "feeds.py:4: xml.etree.ElementTree",
        "feeds.py:5: xml.dom",
        "feeds.py:6: lxml.etree",
        "feeds.py:8: xml.etree.ElementTree.parse",
        "feeds.py:9: Tree.iterparse",
        "feeds.py:10: ET.fromstring",
    ]


def test_a_document_over_the_size_bound_is_refused_before_parsing(monkeypatch):
    # A tree costs many times its text whatever the document's shape, so size
    # is the bound; the parser never sees a larger document.
    from app.security import xml

    def unreachable(*args, **kwargs):
        raise AssertionError("parsed a document over the size bound")

    limit = xml.MAX_DOCUMENT_BYTES
    within = b"<rss>" + b"x" * (limit - 11) + b"</rss>"
    assert len(within) == limit
    assert parse_untrusted_xml(within).text == "x" * (limit - 11)
    monkeypatch.setattr(xml.ET, "fromstring", unreachable)
    monkeypatch.setattr(xml.expat, "ParserCreate", unreachable)
    # Text is held to the bound in the UTF-8 bytes the parser reads, so text
    # within it in characters and over it in bytes is refused too.
    wide = "<rss>" + "\u4e00" * (limit // 3) + "</rss>"
    assert len(wide) < limit < len(wide.encode())
    for document in (within + b" ", (within + b" ").decode(), wide):
        with pytest.raises(ET.ParseError, match="over 2 MiB are refused"):
            parse_untrusted_xml(document)
