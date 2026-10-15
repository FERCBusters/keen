"""Short-lived constrained network process. No database, storage or KEEN secrets."""

import json
import re
import resource
import signal
import sys
import time
from email import policy
from email.parser import BytesHeaderParser

from app.security.redaction import redact_str

from .mime import body_part, decode_body, structure
from .transport import Client

COLLECTION_TIMEOUT = 45
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


def checked(result):
    status, rows = result
    if status != "OK":
        raise ValueError("IMAP command rejected")
    return rows


def number(client, key):
    _, values = client.response(key)
    if not values or not values[0] or not re.fullmatch(rb"[0-9]{1,10}", values[0]):
        raise ValueError("IMAP UID metadata missing")
    if any(value != values[0] for value in values[1:]):
        raise ValueError("Conflicting IMAP UID metadata")
    value = int(values[0])
    if not 1 <= value <= 2**32 - 1:
        raise ValueError("Invalid IMAP UID metadata")
    return value


def literal(client, uid, section, limit):
    client.literal_limit = limit
    try:
        rows = checked(
            client.uid("FETCH", str(uid), f"(UID BODY.PEEK[{section}]<0.{limit}>)")
        )
    finally:
        # A missing literal or rejected command must not leave permission to
        # receive unsolicited data during a subsequent metadata command.
        client.literal_limit = 0
    positions = [i for i, row in enumerate(rows) if isinstance(row, tuple)]
    if len(positions) != 1:
        raise ValueError("Unexpected IMAP FETCH response")
    pos = positions[0]
    prefix, value = rows[pos]
    # UID may appear before or after the literal, but must be in this FETCH.
    # Never accept an unrelated FLAGS response or a quoted MIME value as its UID.
    before = re.fullmatch(
        rb"[1-9][0-9]* \((?:UID ([0-9]+) )?BODY\["
        + re.escape(section.encode("ascii"))
        + rb"\](?:<0>)? \{([0-9]+)\}",
        prefix,
        re.IGNORECASE,
    )
    after = (
        re.fullmatch(rb"(?: UID ([0-9]+))?\)", rows[pos + 1], re.IGNORECASE)
        if pos + 1 < len(rows) and isinstance(rows[pos + 1], bytes)
        else None
    )
    if not before or not after or not isinstance(value, bytes):
        raise ValueError("Unexpected IMAP literal metadata")
    identifiers = [v for v in (before[1], after[1]) if v is not None]
    if (
        identifiers != [str(uid).encode()]
        or int(before[2]) != len(value)
        or len(value) > limit
    ):
        raise ValueError("IMAP literal identity or size mismatch")
    return value


def metadata(data, uid, *, include_body=True):
    match = re.fullmatch(rb"[1-9][0-9]* (\(.*\))", data, re.DOTALL)
    if not match:
        raise ValueError("Invalid IMAP metadata response")
    fields = structure(match[1])
    if (
        not isinstance(fields, list)
        or len(fields) != (6 if include_body else 4)
        or any(not isinstance(k, str) for k in fields[::2])
    ):
        raise ValueError("Invalid IMAP metadata fields")
    values = dict(zip((k.upper() for k in fields[::2]), fields[1::2]))
    expected = {"UID", "RFC822.SIZE"} | ({"BODYSTRUCTURE"} if include_body else set())
    if set(values) != expected or values["UID"] != str(uid):
        raise ValueError("IMAP metadata UID mismatch")
    size = values["RFC822.SIZE"]
    if not isinstance(size, str) or not re.fullmatch(r"[0-9]{1,20}", size):
        raise ValueError("Invalid IMAP message size")
    return int(size), values.get("BODYSTRUCTURE")


def collect(config, cursor, allowed_cidrs):
    client = None
    try:
        client = Client(
            config["host"],
            config["port"],
            allowed_cidrs,
            time.monotonic() + COLLECTION_TIMEOUT,
        )
        username = (
            '"' + config["username"].replace("\\", "\\\\").replace('"', '\\"') + '"'
        )
        checked(client.login(username, config["password"]))
        # Quoted mailbox is constructed from validated printable characters.
        mailbox = '"' + config["folder"].replace("\\", "\\\\").replace('"', '\\"') + '"'
        checked(
            client.select(mailbox, readonly=True)
        )  # EXAMINE; never SELECT/STORE/CLOSE
        validity = number(client, "UIDVALIDITY")
        next_uid = number(client, "UIDNEXT")
        if not cursor:
            return {
                "uidvalidity": validity,
                "last_uid": next_uid - 1,
                "initialised": True,
                "items": [],
            }
        if cursor["uidvalidity"] != validity:
            return {"uidvalidity": validity, "reset_required": True, "items": []}
        last = int(cursor["last_uid"])
        if next_uid - 1 < last:
            raise ValueError("IMAP UIDNEXT moved backwards")
        end = min(last + 100, next_uid - 1)
        if end <= last:
            return {"uidvalidity": validity, "last_uid": last, "items": []}
        raw = checked(client.uid("SEARCH", None, "UID", f"{last + 1}:{end}"))
        if len(raw) != 1 or not isinstance(raw[0], bytes) or len(raw[0]) > 1200:
            raise ValueError("Oversized IMAP search response")
        tokens = raw[0].split()
        if len(tokens) > 100 or any(
            not re.fullmatch(rb"[0-9]{1,10}", x) for x in tokens
        ):
            raise ValueError("Invalid IMAP search response")
        uids = sorted(set(int(x) for x in tokens))
        if any(not last < uid <= end for uid in uids):
            raise ValueError("IMAP returned UID outside requested window")
        items = []
        output_bytes = 4096  # Reserve the small result envelope and separators.
        # At most ten messages per run; remaining identifiers are picked up next run.
        for uid in uids[:10]:
            reserve = (
                min(1024 * 1024, config["body_max_chars"] * 12 + 16)
                if config["fetch_body"]
                else 0
            )
            if client.received + reserve + 131072 > 4 * 1024 * 1024:
                break
            client.literal_limit = 0
            attributes = (
                "(UID RFC822.SIZE BODYSTRUCTURE)"
                if config["fetch_body"]
                else "(UID RFC822.SIZE)"
            )
            meta = checked(client.uid("FETCH", str(uid), attributes))
            if len(meta) != 1 or not isinstance(meta[0], bytes):
                raise ValueError("Unsupported IMAP metadata response")
            size, tree = metadata(meta[0], uid, include_body=config["fetch_body"])
            part = body_part(tree) if config["fetch_body"] else None
            header = literal(
                client, uid, "HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)", 16384
            )
            if len(header) >= 16384 or header.count(b"\n") > 200:
                raise ValueError("IMAP headers exceed limit")
            parsed = BytesHeaderParser(policy=policy.default).parsebytes(header)
            item = {
                "uid": uid,
                "message_size": size,
                "headers": {
                    name: redact_str(str(parsed.get(name, "")))[:2048]
                    for name in ("subject", "from", "to", "date", "message-id")
                },
                "body": None,
                "body_omitted_reason": "disabled",
                "body_truncated": False,
                "attachments_fetched": False,
            }
            if config["fetch_body"]:
                item["body_omitted_reason"] = "no_supported_inline_body"
                if part:
                    # Wire bound independent of server size, with enough bytes for common encodings.
                    limit = min(1024 * 1024, config["body_max_chars"] * 12 + 16)
                    raw_body = literal(client, uid, part["section"], limit)
                    try:
                        text, truncated, fallback = decode_body(
                            raw_body, part, config["body_max_chars"]
                        )
                    except ValueError:
                        # Identity/transport checks above remain fatal. Bad content
                        # cannot trap every later message behind the same UID.
                        item["body_omitted_reason"] = (
                            "invalid_or_unsupported_body_encoding"
                        )
                    else:
                        item.update(
                            body=text,
                            body_content_type=part["content_type"],
                            body_truncated=truncated
                            or len(raw_body) >= limit
                            or part["size"] > len(raw_body),
                            charset_fallback=fallback,
                            body_omitted_reason=None,
                        )
            # JSON escaping can expand a valid bounded Unicode body considerably.
            # Keep a resumable batch rather than rejecting all its messages later.
            item_bytes = len(json.dumps(item, ensure_ascii=True).encode()) + 2
            if output_bytes + item_bytes > MAX_OUTPUT_BYTES:
                break
            output_bytes += item_bytes
            items.append(item)
        return {
            "uidvalidity": validity,
            "last_uid": (items[-1]["uid"] if items else last)
            if len(items) < len(uids)
            else end,
            "items": items,
        }
    finally:
        if client:
            # No LOGOUT wait, CLOSE, EXPUNGE, STORE, IDLE, referrals or compression.
            client.shutdown()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    signal.alarm(50)
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("Worker input exceeds limit")
        request = json.loads(raw)
        # A buffered read can keep receiving small chunks and reset the socket
        # timeout. Enforce wall time independently of progress in the read loop.
        signal.alarm(COLLECTION_TIMEOUT)
        result = collect(
            request["configuration"], request["cursor"], request["allowed_cidrs"]
        )
        signal.alarm(5)  # Bound serialisation/output after the network phase.
        output = json.dumps(result, ensure_ascii=True).encode()
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError("Worker output exceeds limit")
        sys.stdout.buffer.write(output)
    except Exception:
        # Never return server responses, credentials, message text or tracebacks.
        sys.stdout.write(
            '{"error":"IMAP collection failed a connection, protocol or resource check"}'
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
