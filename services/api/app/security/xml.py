"""One parsing boundary for XML documents returned by evidence sources.

A document type declaration's internal subset can declare entities and default
attribute values, which multiply a document's size while it is parsed: a feed
within the collection response limit could otherwise occupy gigabytes once its
tree is built. Expat's own amplification limit covers entities only, and lets
each entity grow up to a hundredfold. Feeds have no use for an internal subset,
so such documents are refused before a tree is built. A declaration that names
only an external DTD remains accepted; that DTD is never fetched.
"""

from xml.etree import ElementTree as ET
from xml.parsers import expat


class _PrologEnd(Exception):
    """The root element has begun, so no document type declaration follows."""


def _refuse_internal_subset(
    name: str, system_id: str | None, public_id: str | None, has_internal_subset: bool
) -> None:
    if has_internal_subset:
        raise ET.ParseError("XML documents with an internal DTD subset are refused")


def _end_prolog(name: str, attributes: dict[str, str]) -> None:
    raise _PrologEnd


def parse_untrusted_xml(data: str | bytes) -> ET.Element:
    prolog = expat.ParserCreate()
    prolog.StartDoctypeDeclHandler = _refuse_internal_subset
    prolog.StartElementHandler = _end_prolog
    try:
        # Reads only the prolog: parsing stops as the root element begins.
        prolog.Parse(data, True)
    except _PrologEnd:
        pass
    except expat.ExpatError as exc:
        error = ET.ParseError(str(exc))
        error.code, error.position = exc.code, (exc.lineno, exc.offset)
        raise error from None
    return ET.fromstring(data)
