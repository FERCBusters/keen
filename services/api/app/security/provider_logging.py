"""Suppress provider diagnostics that contain URLs, bodies or reflected secrets.

Apply to the emitting logger: filters on a parent do not cover child loggers.
KEEN exposes its own fixed collection diagnostics instead. Recheck SDK logging
sites when upgrading dependencies; arbitrary wire-debug modes are unsupported.
"""

import logging


class _SuppressProviderLogs(logging.Filter):
    def filter(self, record):
        return False


_FILTER = _SuppressProviderLogs()


def suppress_provider_logs(*names):
    for name in names:
        logging.getLogger(name).addFilter(_FILTER)
