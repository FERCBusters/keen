"""One email address, as Keen stores it and sends to it.

An address reaches an SMTP To header, where a comma or semicolon starts another
address and angle brackets, quotes or parentheses wrap one in a display name or
comment; smtplib then sends to every address the header lists. Every address
Keen accepts into a record, reads from an identity provider or directory, or
sends to passes through single_mailbox, so a field names exactly one mailbox.
"""

from email.utils import getaddresses

MAX_EMAIL_LENGTH = 256
# RFC 5322 specials outside a quoted local part, which Keen does not accept.
_SPECIALS = frozenset('<>()[]:;,"\\')


def single_mailbox(raw: object) -> str | None:
    """The address, without surrounding space, when it is exactly one mailbox.

    Anything else (an empty value, a list of addresses, a display name or a
    comment, whitespace or control characters inside, or more than one @)
    gives None.
    """
    value = str(raw or "").strip()
    if not value or len(value) > MAX_EMAIL_LENGTH:
        return None
    local, at, domain = value.rpartition("@")
    if (
        not at
        or not local
        or not domain
        or "@" in local
        or domain.startswith(".")
        or domain.endswith(".")
        or ".." in domain
        or any(c.isspace() or not c.isprintable() or c in _SPECIALS for c in value)
    ):
        return None
    # The mail library reads the value as the one address it is checked as.
    if getaddresses([value]) != [("", value)]:
        return None
    return value
