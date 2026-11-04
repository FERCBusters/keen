"""Bounds on the shape of parsed JSON from requests and collected evidence.

Walking a parsed document recursively costs a stack frame for each level, and
validating it costs time for each value. A document is measured here, without
recursion, before anything recurses into it or validates it.
"""

from typing import Any

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
