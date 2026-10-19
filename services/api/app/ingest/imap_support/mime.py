"""Bounded BODYSTRUCTURE inspection. HTML is never parsed or rendered."""

import base64
import binascii
import quopri

from app.security.redaction import redact_str


def structure(raw):
    if len(raw) > 32768:
        raise ValueError("IMAP structure exceeds limit")
    position = 0
    count = 0

    def parse(depth=0):
        nonlocal position, count
        count += 1
        if depth > 16 or count > 2048:
            raise ValueError("IMAP structure complexity exceeds limit")
        while position < len(raw) and raw[position : position + 1] == b" ":
            position += 1
        if position >= len(raw):
            raise ValueError("Incomplete IMAP structure")
        char = raw[position : position + 1]
        position += 1
        if char == b"(":
            result = []
            while True:
                while position < len(raw) and raw[position : position + 1] == b" ":
                    position += 1
                if position >= len(raw):
                    raise ValueError("Incomplete IMAP structure")
                if raw[position : position + 1] == b")":
                    position += 1
                    return result
                result.append(parse(depth + 1))
        if char == b'"':
            out = bytearray()
            while position < len(raw):
                ch = raw[position]
                position += 1
                if ch == 34:
                    return bytes(out).decode("ascii", "replace")
                if ch == 92:
                    if position >= len(raw):
                        break
                    ch = raw[position]
                    position += 1
                out.append(ch)
            raise ValueError("Unterminated IMAP string")
        start = position - 1
        while position < len(raw) and raw[position : position + 1] not in (b" ", b")"):
            position += 1
        atom = raw[start:position]
        if not atom or atom.startswith((b"{", b")")):
            raise ValueError("Unsupported IMAP structure literal")
        return None if atom.upper() == b"NIL" else atom.decode("ascii", "replace")

    result = parse()
    if raw[position:].strip():
        raise ValueError("Trailing IMAP structure data")
    return result


def parameters(value):
    if value is None:
        return {}
    if (
        not isinstance(value, list)
        or len(value) % 2
        or any(not isinstance(v, str) for v in value)
    ):
        raise ValueError("Invalid MIME parameters")
    return {str(value[i]).lower(): str(value[i + 1]) for i in range(0, len(value), 2)}


def attached(disposition):
    if disposition is None:
        return False
    if (
        not isinstance(disposition, list)
        or len(disposition) != 2
        or not isinstance(disposition[0], str)
    ):
        raise ValueError("Invalid MIME disposition")
    return disposition[0].lower() != "inline" or any(
        k.startswith("filename") for k in parameters(disposition[1])
    )


def body_part(tree):
    """Select at most one inline plain/HTML part; do not enter attached messages."""
    found = []
    visited = 0

    def visit(node, path="", depth=0):
        nonlocal visited
        visited += 1
        if visited > 100 or depth > 12:
            raise ValueError("Too many MIME parts")
        if not isinstance(node, list) or len(node) < 2:
            raise ValueError("Invalid MIME structure")
        if isinstance(node[0], list):
            children = 0
            while children < len(node) and isinstance(node[children], list):
                children += 1
            params = parameters(
                node[children + 1] if len(node) > children + 1 else None
            )
            if any(k == "name" or k.startswith("name*") for k in params):
                return
            disposition = node[children + 2] if len(node) > children + 2 else None
            if attached(disposition):
                return
            if children >= len(node) or not isinstance(node[children], str):
                raise ValueError("Missing MIME multipart subtype")
            if node[children].lower() == "related":
                # Related siblings are resources for the root (e.g. text files
                # linked by HTML), not alternative message bodies.
                root = 0
                if "start" in params:
                    matches = [
                        i
                        for i, child in enumerate(node[:children])
                        if len(child) > 3
                        and isinstance(child[0], str)
                        and child[3] == params["start"]
                    ]
                    if len(matches) != 1:
                        return  # Root identity cannot be established safely.
                    root = matches[0]
                visit(
                    node[root],
                    f"{path}.{root + 1}" if path else str(root + 1),
                    depth + 1,
                )
                return
            for i, child in enumerate(node[:children], 1):
                visit(child, f"{path}.{i}" if path else str(i), depth + 1)
            return
        if (
            len(node) < 8
            or str(node[0]).lower() != "text"
            or str(node[1]).lower() not in ("plain", "html")
        ):
            return
        params = parameters(node[2])
        disposition = node[9] if len(node) > 9 else None
        if "name" in params or any(k.startswith("name*") for k in params):
            return
        if attached(disposition):
            return
        try:
            size = int(node[6])
        except (ValueError, TypeError):
            raise ValueError("Invalid MIME part size") from None
        if size < 0 or size > 2**63 - 1:
            raise ValueError("Invalid MIME part size")
        found.append(
            dict(
                section=path or "1",
                content_type="text/" + str(node[1]).lower(),
                charset=params.get("charset", "utf-8"),
                encoding=str(node[5]).lower(),
                size=size,
            )
        )

    visit(tree)
    return next(
        (p for p in found if p["content_type"] == "text/plain"),
        found[0] if found else None,
    )


def decode_body(raw, part, max_chars):
    encoding = part["encoding"]
    if encoding == "base64":
        # Partial fetches may end between base64 quanta; decode complete quanta only.
        raw = b"".join(raw.split())
        raw = raw[: len(raw) // 4 * 4]
        try:
            raw = base64.b64decode(raw, validate=True)
        except binascii.Error:
            raise ValueError("Invalid base64 MIME body") from None
    elif encoding == "quoted-printable":
        raw = quopri.decodestring(raw)
    elif encoding not in ("7bit", "8bit", "binary"):
        raise ValueError("Unsupported body transfer encoding")
    # Fixed codec allowlist avoids arbitrary codec lookup/expensive transformations.
    charset = part["charset"].lower().replace("_", "-")
    aliases = {
        "utf8": "utf-8",
        "us-ascii": "ascii",
        "iso-8859-1": "latin-1",
        "windows-1252": "cp1252",
    }
    charset = aliases.get(charset, charset)
    if charset not in {"utf-8", "ascii", "latin-1", "cp1252"}:
        raise ValueError("Unsupported body charset")
    text = raw.decode(charset, "replace")
    # Redact the bounded decoded input before a display limit can split a value.
    text = redact_str(text)
    return text[:max_chars], len(text) > max_chars
