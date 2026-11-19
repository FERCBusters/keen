"""Bounds on the shape of parsed JSON from requests and collected evidence.

Walking a parsed document recursively costs a stack frame for each level, and
validating it costs time for each value. A document is measured here, without
recursion, before anything recurses into it or validates it.
"""

import json
from typing import Any

from app.security.regex import scan_pattern

# The deepest nesting of objects and arrays a request body may have, and the
# deepest a collected structure is walked. Evidence nested more deeply keeps
# its deeper content as text, or as NESTED_TOO_DEEPLY where it is stored as data.
MAX_DEPTH = 64
NESTED_TOO_DEEPLY = "[nested too deeply]"


def json_depth(
    value: Any, limit: int = MAX_DEPTH, *, max_values: int | None = None
) -> int | None:
    """The container depth of a parsed document, or None when it is too large.

    None means the document nests more than ``limit`` objects and arrays, or
    holds more than ``max_values`` values in all (the document itself, each
    array item and each object member).
    """
    deepest, count = 0, 0
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        count += 1
        if max_values is not None and count > max_values:
            return None
        if isinstance(item, (dict, list, tuple)):
            if depth > limit:
                return None
            deepest = max(deepest, depth)
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)
    return deepest


# The most values a JSON text from outside Keen is decoded into where it is
# decoded whole: each value costs tens to hundreds of bytes as Python objects,
# many times its text, and copies made while redacting or masking it multiply
# that again. A larger text is handled as text, as one that is not JSON is.
MAX_DECODED_VALUES = 100_000


def decodes_within(text: str | bytes, limit: int = MAX_DECODED_VALUES) -> bool:
    """Whether a JSON text holds at most ``limit`` values, read without decoding.

    Each value after the first in an array or object follows a comma, and each
    array or object opens with a bracket, so these counts bound the values
    json_depth counts. Commas and brackets inside strings only raise the count.
    """
    if isinstance(text, str):
        found = text.count(",") + text.count("[") + text.count("{")
    else:
        found = text.count(b",") + text.count(b"[") + text.count(b"{")
    return found + 1 <= limit


# Characters that open, close or quote JSON structure, and the string escape.
_STRUCTURE = scan_pattern(r'[\[\]{}"\\]')
_MARKER = json.dumps(NESTED_TOO_DEEPLY)


def decode_json(data: str | bytes | bytearray) -> Any:
    """``json.loads`` for a document from outside Keen, however deeply it nests.

    The interpreter's parser recurses for each level and stops with
    RecursionError at a depth the interpreter sets, so one value nested deeply
    enough would leave the whole document, such as every record on a page,
    unreadable. Such a document is read again with each object or array nested
    more than MAX_DEPTH deep replaced by NESTED_TOO_DEEPLY, as storing replaces
    it anyway. Text that is not JSON still raises json.JSONDecodeError.
    """
    try:
        return json.loads(data)
    except RecursionError:
        pass
    if not isinstance(data, str):
        data = bytes(data).decode(json.detect_encoding(data), "surrogatepass")
    try:
        return json.loads(_shallow(data))
    except RecursionError:
        # Only containers left open to the end are still too deep.
        raise json.JSONDecodeError("Unterminated nesting", data, len(data)) from None


def _shallow(text: str) -> str:
    """The JSON text with each container nested more than MAX_DEPTH deep
    replaced by NESTED_TOO_DEEPLY, read in one pass without recursion."""
    pieces, depth, copied, cut = [], 0, 0, None
    in_string, escaped = False, -1
    for match in _STRUCTURE.finditer(text):
        at, char = match.start(), match.group()
        if at == escaped:
            continue
        if in_string:
            if char == "\\":
                escaped = at + 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH and cut is None:
                pieces.append(text[copied:at])
                cut, copied = depth, at
        elif char in "]}":
            if depth == cut:
                pieces.append(_MARKER)
                cut, copied = None, at + 1
            depth -= 1
    # Text left unbalanced stays as it was, so the parser reports it.
    pieces.append(text[copied:])
    return "".join(pieces)
