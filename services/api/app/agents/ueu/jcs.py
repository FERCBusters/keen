"""RFC 8785 JSON canonicalisation for KEEN's UEU profile.

The profile admits integers only, within the range every JSON implementation
reads exactly, so numbers never need ECMAScript float formatting. Parsing is
strict: duplicate member names, non-integers, NaN and lone surrogates are
rejected, because a verifier and a producer must agree on one meaning for the
bytes that were signed.
"""

from __future__ import annotations

import json
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 32


class CanonicalError(ValueError):
    """The value cannot be represented in the profile's canonical form."""


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise CanonicalError("duplicate member name")
        out[key] = value
    return out


def _no_float(_value):
    raise CanonicalError("only integers are allowed")


def _no_constant(_value):
    raise CanonicalError("NaN and Infinity are not JSON")


def parse(data: str | bytes) -> Any:
    """Decode JSON under the profile's rules."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CanonicalError("invalid UTF-8") from exc
    try:
        return json.loads(
            data,
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except RecursionError as exc:
        raise CanonicalError("nesting too deep") from exc
    except json.JSONDecodeError as exc:
        raise CanonicalError("invalid JSON") from exc


def _string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalError("lone surrogate") from exc
    # Python escapes exactly the characters RFC 8785 (ECMAScript) escapes:
    # quotation mark, reverse solidus, \b \f \n \r \t and other controls as
    # lower-case \u00xx; everything else is emitted as UTF-8.
    return json.dumps(value, ensure_ascii=False)


def _write(value: Any, out: list[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise CanonicalError("nesting exceeds 32 levels")
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalError("integer outside the exact range")
        out.append(str(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _write(item, out, depth + 1)
        out.append("]")
    elif isinstance(value, dict):
        keys = list(value)
        if any(not isinstance(k, str) for k in keys):
            raise CanonicalError("member names must be strings")
        # Members are ordered by UTF-16 code units; big-endian UTF-16 bytes
        # compare in the same order.
        try:
            keys.sort(key=lambda k: k.encode("utf-16-be"))
        except UnicodeEncodeError as exc:
            raise CanonicalError("lone surrogate") from exc
        out.append("{")
        for i, key in enumerate(keys):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _write(value[key], out, depth + 1)
        out.append("}")
    else:
        raise CanonicalError("unsupported value")


def canonical(value: Any) -> bytes:
    """Return the canonical UTF-8 bytes for a value."""
    out: list[str] = []
    try:
        _write(value, out, 0)
    except RecursionError as exc:
        raise CanonicalError("nesting too deep") from exc
    return "".join(out).encode("utf-8")


def parse_canonical(data: str) -> tuple[Any, bytes]:
    """Parse a value whose text must already be canonical.

    Returns the value and its exact bytes. Any other spelling of the same
    value is rejected, so the stored bytes are the bytes that were hashed.
    """
    try:
        raw = data.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalError("lone surrogate") from exc
    value = parse(data)
    if canonical(value) != raw:
        raise CanonicalError("not in canonical form")
    return value, raw
