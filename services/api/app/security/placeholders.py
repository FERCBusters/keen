"""Example-file placeholders are never credentials.

A value such as ``<GENERATE_STRONG_PASSWORD>`` tells an operator what to put in
its place. Left as it is, it is a secret anyone can read in the example file,
so nothing that checks a credential accepts one.
"""


def is_placeholder(value: str) -> bool:
    """Whether ``value`` is written as a placeholder: text in angle brackets."""
    text = value.strip()
    return len(text) >= 2 and text.startswith("<") and text.endswith(">")
