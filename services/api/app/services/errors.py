"""Expected application failures, safe to expose through an HTTP adapter.

Only explicitly raised errors belong here. Unexpected exceptions retain the
normal internal-error handling. Status codes preserve the public API contract.
"""

from typing import Any


class DomainError(Exception):
    """``detail`` is a message, or an object naming what was refused."""

    def __init__(
        self,
        status_code: int,
        detail: str | dict[str, Any],
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(detail)
