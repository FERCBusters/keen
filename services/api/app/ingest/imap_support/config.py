"""Validate both database and file-managed IMAP connections before network use."""

import re

MAX_BODY_CHARS = 65536
DEFAULT_BODY_CHARS = 4000


def configuration(values):
    values = dict(values or {})
    if set(values) - {
        "host",
        "port",
        "username",
        "folder",
        "fetch_body",
        "body_max_chars",
        "poll_interval_minutes",
    }:
        raise ValueError("Unsupported IMAP configuration field")
    host = values.get("host", "")
    if not isinstance(host, str):
        raise ValueError("IMAP hostname must be text")
    host = host.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", host):
        raise ValueError("Enter an IMAP hostname or IP address, without a URL")
    username = values.get("username", "")
    folder = values.get("folder") or "INBOX"
    for label, value in (("username", username), ("folder", folder)):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 256
            or any(ord(c) < 32 or ord(c) > 126 for c in value)
        ):
            raise ValueError(f"IMAP {label} must be 1–256 printable ASCII characters")
    try:
        raw_port = values.get("port") or "993"
        raw_chars = values.get("body_max_chars") or str(DEFAULT_BODY_CHARS)
        if any(not re.fullmatch(r"[0-9]{1,5}", str(v)) for v in (raw_port, raw_chars)):
            raise ValueError("Invalid integer")
        port = int(raw_port)
        chars = int(raw_chars)
    except (TypeError, ValueError):
        raise ValueError(
            "IMAP port and body character limit must be integers"
        ) from None
    if not 1 <= port <= 65535 or not 1 <= chars <= MAX_BODY_CHARS:
        raise ValueError(
            "IMAP port must be 1–65535; body character limit must be 1–65536"
        )
    interval = values.get("poll_interval_minutes", "5")
    if not isinstance(interval, str) or not re.fullmatch(r"[0-9]{1,4}", interval):
        raise ValueError("IMAP collection interval must be whole minutes")
    interval = int(interval)
    if not 5 <= interval <= 1440 or interval % 5:
        raise ValueError(
            "IMAP collection interval must be 5–1440 minutes in steps of 5"
        )
    body = values.get("fetch_body") or "false"
    if body not in ("true", "false"):
        raise ValueError("IMAP fetch body must be true or false")
    return dict(
        host=host,
        port=port,
        username=username,
        folder=folder,
        fetch_body=body == "true",
        body_max_chars=chars,
        poll_interval_minutes=interval,
    )
