"""Validation for external links displayed to users (no network request)."""

from urllib.parse import urlsplit


def external_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        if (
            len(value) > 2048
            or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError()
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
        ):
            raise ValueError()
        _ = url.port
    except ValueError:
        raise ValueError(
            "Use an HTTP or HTTPS URL without embedded credentials or control characters"
        ) from None
    return value
