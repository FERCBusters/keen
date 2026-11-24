"""Short-lived constrained network process. No database, storage or Keen secrets."""

import json
import os
import re
import resource
import signal
import sys
import time
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser

from mosp.security.redaction import mask_mail_address_headers, redact_str
from mosp.security.regex import scan_pattern

from .mime import ContentLimit, body_part, decode_body, structure
from .transport import Client, ResponseLimit

COLLECTION_TIMEOUT = 45
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


FAILED = "IMAP collection failed a connection, protocol or resource check"


class MessageUnreadable(ValueError):
    """A message's own data ended the run: its metadata could not be read.
    Runs that end so at the same message count towards passing it."""


def report(uid=None):
    """The fixed failure report: never server responses, credentials, message
    text or tracebacks, and the message being read by UID alone."""
    return json.dumps({"error": FAILED, **({"failed_uid": uid} if uid else {})})


def cpu_spent(signum, frame):
    # Reading a message used up the worker's CPU time. No handler that reads
    # content may catch this, so the report is written and the process ends.
    os.write(1, report(PROGRESS["uid"]).encode())
    os._exit(1)


def checked(result):
    status, rows = result
    if status != "OK":
        raise ValueError("IMAP command rejected")
    return rows


def number(client, key):
    _, values = client.response(key)
    if (
        not values
        or not values[0]
        or not scan_pattern(rb"[0-9]{1,10}").fullmatch(values[0])
    ):
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
    before = scan_pattern(
        rb"[1-9][0-9]* \((?:UID ([0-9]+) )?BODY\["
        + re.escape(section.encode("ascii"))
        + rb"\](?:<0>)? \{([0-9]+)\}",
        re.IGNORECASE,
    ).fullmatch(prefix)
    after = (
        scan_pattern(rb"(?: UID ([0-9]+))?\)", re.IGNORECASE).fullmatch(rows[pos + 1])
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
    match = scan_pattern(rb"[1-9][0-9]* (\(.*\))", re.DOTALL).fullmatch(data)
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
    if not isinstance(size, str) or not scan_pattern(r"[0-9]{1,20}").fullmatch(size):
        raise ValueError("Invalid IMAP message size")
    return int(size), values.get("BODYSTRUCTURE")


# The message being read, for the error a failed run reports.
PROGRESS: dict = {"uid": None}


def omitted(uid, reason):
    """A message recorded by its UID alone, without its headers or body."""
    return {
        "uid": uid,
        "message_size": None,
        "headers": dict.fromkeys(("subject", "from", "to", "date", "message-id"), ""),
        "body": None,
        "body_omitted_reason": reason,
        "body_truncated": False,
        "attachments_fetched": False,
    }


def message(client, uid, config):
    """One message's metadata, selected headers and, optionally, inline body."""
    client.literal_limit = 0
    attributes = (
        "(UID RFC822.SIZE BODYSTRUCTURE)"
        if config["fetch_body"]
        else "(UID RFC822.SIZE)"
    )
    meta = checked(client.uid("FETCH", str(uid), attributes))
    # A server may add an unsolicited FETCH of another message's flags.
    meta = [
        row for row in meta if isinstance(row, bytes) and b"RFC822.SIZE" in row.upper()
    ]
    if len(meta) != 1:
        raise ValueError("Unsupported IMAP metadata response")
    try:
        size, tree = metadata(meta[0], uid, include_body=config["fetch_body"])
    except ValueError as error:
        raise MessageUnreadable(str(error)) from None
    try:
        part = body_part(tree) if config["fetch_body"] else None
    except ValueError as error:
        # The sender chose the message's MIME structure; the session is in step.
        raise ContentLimit(str(error)) from None
    header = literal(
        client, uid, "HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)", 16384
    )
    if len(header) >= 16384 or header.count(b"\n") > 200:
        raise ContentLimit("IMAP headers exceed limit")
    try:
        # Read as plain text: the address headers are masked whole, and a
        # structured parse of a sender's address list can take seconds.
        parsed = BytesHeaderParser(policy=policy.compat32).parsebytes(header)
        fields = {
            name: " ".join(str(parsed.get(name, "")).split())
            for name in ("subject", "from", "to", "date", "message-id")
        }
        try:
            fields["subject"] = str(make_header(decode_header(fields["subject"])))
        except Exception:
            pass  # An undecodable subject is kept as written.
        # Address headers are masked before the character limit can split an
        # address, whether or not optional event-data masking is enabled.
        headers = mask_mail_address_headers(
            {name: redact_str(value) for name, value in fields.items()}
        )
    except Exception:
        # The sender wrote these fields; a parser that cannot read them must
        # not hold back the mail after this message.
        raise ContentLimit("Unreadable IMAP headers") from None
    item = {
        "uid": uid,
        "message_size": size,
        "headers": {name: value[:2048] for name, value in headers.items()},
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
                text, truncated = decode_body(raw_body, part, config["body_max_chars"])
            except ValueError:
                # Identity/transport checks above remain fatal. Bad content
                # cannot trap every later message behind the same UID.
                item["body_omitted_reason"] = "invalid_or_unsupported_body_encoding"
            else:
                item.update(
                    body=text,
                    body_content_type=part["content_type"],
                    body_truncated=truncated
                    or len(raw_body) >= limit
                    or part["size"] > len(raw_body),
                    body_omitted_reason=None,
                )
    return item


def collect(config, cursor, allowed_cidrs):
    client = None
    PROGRESS["uid"] = None
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
        raw = checked(client.uid("SEARCH", "UID", f"{last + 1}:{end}"))
        if len(raw) != 1 or not isinstance(raw[0], bytes) or len(raw[0]) > 1200:
            raise ValueError("Oversized IMAP search response")
        tokens = raw[0].split()
        if len(tokens) > 100 or any(
            not scan_pattern(rb"[0-9]{1,10}").fullmatch(x) for x in tokens
        ):
            raise ValueError("Invalid IMAP search response")
        uids = sorted(set(int(x) for x in tokens))
        if any(not last < uid <= end for uid in uids):
            raise ValueError("IMAP returned UID outside requested window")
        items: list[dict] = []
        output_bytes = 4096  # Reserve the small result envelope and separators.
        # At most ten messages per run; remaining identifiers are picked up next run.
        skip = cursor.get("skip_uids") if isinstance(cursor, dict) else None
        skip = (
            {uid for uid in skip if type(uid) is int}
            if isinstance(skip, list)
            else set()
        )
        for uid in uids[:10]:
            reserve = (
                min(1024 * 1024, config["body_max_chars"] * 12 + 16)
                if config["fetch_body"]
                else 0
            )
            if client.received + reserve + 131072 > 4 * 1024 * 1024:
                break
            PROGRESS["uid"] = uid
            if uid in skip:
                # This message failed every run that reached it, in a way no
                # session survives; it is recorded without its content.
                item = omitted(uid, "message_failed_repeatedly")
            else:
                try:
                    item = message(client, uid, config)
                except ContentLimit:
                    # Its sender chose content past a bound; one message must
                    # not hold back the mail after it.
                    item = omitted(uid, "message_exceeds_limits")
                except (MessageUnreadable, ResponseLimit):
                    if not items:
                        raise
                    # The mail before it is kept; the next run starts at it.
                    PROGRESS["uid"] = None
                    return {
                        "uidvalidity": validity,
                        "last_uid": items[-1]["uid"],
                        "items": items,
                    }
            # JSON escaping can expand a valid bounded Unicode body considerably.
            # Keep a resumable batch rather than rejecting all its messages later.
            item_bytes = len(json.dumps(item, ensure_ascii=True).encode()) + 2
            if output_bytes + item_bytes > MAX_OUTPUT_BYTES:
                break
            output_bytes += item_bytes
            items.append(item)
        PROGRESS["uid"] = None
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
    # The soft limit names the message being read and ends the process; the
    # hard one ends it if even that cannot run.
    signal.signal(signal.SIGXCPU, cpu_spent)
    resource.setrlimit(resource.RLIMIT_CPU, (8, 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    signal.alarm(50)
    PROGRESS["uid"] = None
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
    except Exception as error:
        # The message being read, by UID alone, lets repeated failures at one
        # message be told apart from a connection or server fault. Only a
        # message's own data counts; a timeout, a lost connection or a
        # server's refusal is no reason to pass the message it reached.
        counted = isinstance(error, (MessageUnreadable, ResponseLimit))
        sys.stdout.write(report(PROGRESS["uid"] if counted else None))
        sys.exit(1)


if __name__ == "__main__":
    main()
