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


def checked(result):
    status, rows = result
    if status != "OK":
        raise ValueError("IMAP command rejected")
    return rows


def number(client, key):
    _, values = client.response(key)
    if not values or not values[0] or not re.fullmatch(rb"[0-9]{1,10}", values[0]):
        raise ValueError("IMAP UID metadata missing")
    value = int(values[0])
    if not 1 <= value <= 2**32 - 1:
        raise ValueError("Invalid IMAP UID metadata")
    return value


def literal(client, uid, section, limit):
    client.literal_limit = limit
    rows = checked(
        client.uid("FETCH", str(uid), f"(UID BODY.PEEK[{section}]<0.{limit}>)")
    )
    values = [row[1] for row in rows if isinstance(row, tuple)]
    headers = b" ".join(
        row[0] if isinstance(row, tuple) else row or b"" for row in rows
    )
    if (
        not re.search(rb"\bUID " + str(uid).encode() + rb"\b", headers)
        or len(values) != 1
    ):
        raise ValueError("Unexpected IMAP FETCH response")
    if len(values[0]) > limit:
        raise ValueError("Oversized IMAP body")
    return values[0]


def collect(config, cursor, allowed_cidrs):
    client = None
    try:
        client = Client(
            config["host"], config["port"], allowed_cidrs, time.monotonic() + 45
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
        # At most ten messages per run; remaining identifiers are picked up next run.
        for uid in uids[:10]:
            reserve = (
                min(1024 * 1024, config["body_max_chars"] * 12 + 16)
                if config["fetch_body"]
                else 0
            )
            if client.received + reserve + 131072 > 4 * 1024 * 1024:
                break
            client.literal_limit = 16384
            meta = checked(
                client.uid("FETCH", str(uid), "(UID RFC822.SIZE BODYSTRUCTURE)")
            )
            if len(meta) != 1 or not isinstance(meta[0], bytes):
                raise ValueError("Unsupported IMAP metadata response")
            data = meta[0]
            if not re.search(rb"\bUID " + str(uid).encode() + rb"\b", data):
                raise ValueError("IMAP UID mismatch")
            size = re.search(rb"\bRFC822.SIZE ([0-9]{1,20})\b", data)
            pos = data.find(b"BODYSTRUCTURE ")
            if not size or pos < 0:
                raise ValueError("Missing IMAP structure")
            part = body_part(structure(data[pos + 14 :]))
            header = literal(
                client, uid, "HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)", 16384
            )
            if len(header) >= 16384 or header.count(b"\n") > 200:
                raise ValueError("IMAP headers exceed limit")
            parsed = BytesHeaderParser(policy=policy.default).parsebytes(header)
            item = {
                "uid": uid,
                "message_size": int(size[1]),
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
                    text, truncated, fallback = decode_body(
                        raw_body, part, config["body_max_chars"]
                    )
                    item.update(
                        body=text,
                        body_content_type=part["content_type"],
                        body_truncated=truncated
                        or len(raw_body) >= limit
                        or part["size"] > len(raw_body),
                        charset_fallback=fallback,
                        body_omitted_reason=None,
                    )
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
        result = collect(
            request["configuration"], request["cursor"], request["allowed_cidrs"]
        )
        output = json.dumps(result, ensure_ascii=True).encode()
        if len(output) > 5 * 1024 * 1024:
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
