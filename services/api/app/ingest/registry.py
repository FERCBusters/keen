"""The source types Keen collects from, each declared by its own module.

A source module describes itself once, beside its collector::

    register(Source(name="jenkins", module=__name__, ...))

Everything that needs to know about source types reads this registry: the
worker's queues, run budgets and schedule, the pause switches, the connection
forms, the evidence definitions and the administration pages. Adding a source
is adding its module; no list elsewhere names it.

This module imports nothing from the application but its settings, so any
module may import it. The source modules themselves are imported the first
time the registry is read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules

from app.core.config import settings

# The queue of the worker that makes outbound HTTP requests for collectors.
HTTP_QUEUE = "ingest-http"
# The queue of the isolated mail worker.
IMAP_QUEUE = "imap"


@dataclass(frozen=True)
class Source:
    """One source type and what the rest of Keen needs to know about it."""

    name: str
    # The module that declares the source, and holds its collector if it has one.
    module: str
    # The worker queue its collection runs on; None for a source Keen is sent
    # evidence by (webhooks) rather than one it collects from.
    queue: str | None = None
    # How often beat starts a collection of every connection: a number of
    # seconds, or the minutes of each hour as a crontab expression ("*/30").
    # None when another task dispatches the source's runs.
    schedule: float | str | None = None
    # The settings field naming the file of shipped collection defaults.
    config_setting: str | None = None
    # A connection's endpoint and account fields, and its secret fields.
    public_fields: tuple[str, ...] = ()
    secret_fields: tuple[str, ...] = ()
    # Every section of the configuration document that lists things to collect.
    input_sections: tuple[str, ...] = ()
    # The section new collection items are added to, and the field naming one.
    section: str | None = None
    id_field: str | None = None
    # The sections whose items an administrator edits as evidence definitions,
    # in the order they are listed.
    definition_sections: tuple[str, ...] = ()
    # The field that identifies an item of those sections to mapping rules.
    key_field: str | None = None
    # Whether a demonstration installation offers the source.
    demo: bool = True
    # Refuses a configuration document the collector cannot run, with a
    # ValueError whose message is safe to show the administrator.
    validate_config: Callable[[dict], object] | None = None

    @property
    def collected(self) -> bool:
        """Whether Keen runs a collector for the source."""
        return self.queue is not None

    @property
    def polled(self) -> bool:
        """Whether its collector runs on the HTTP ingestion worker."""
        return self.queue == HTTP_QUEUE

    @property
    def task_name(self) -> str:
        """The Celery task that collects every connection of the source."""
        return f"app.worker.tasks.ingest_{self.name}_all_task"

    @property
    def enabled(self) -> bool:
        """Whether this installation collects from the source."""
        if settings.demo_mode and not self.demo:
            return False
        return bool(getattr(settings, f"{self.name}_enabled"))

    @property
    def run_seconds(self) -> int:
        """The time one scheduled run of all its connections may take."""
        return int(getattr(settings, f"{self.name}_run_seconds"))

    @property
    def config_path(self) -> str:
        return getattr(settings, self.config_setting or "")

    def collect(self, db, **options):
        """Run the source's collector, found by name so a test can replace it."""
        entry = getattr(import_module(self.module), f"ingest_{self.name}_all")
        return entry(db, **options)


_sources: dict[str, Source] = {}
_loaded = False


def register(source: Source) -> Source:
    """Declare a source type; each name is declared once."""
    known = _sources.get(source.name)
    if known is not None and known != source:
        raise ValueError(f"Source type {source.name} is declared twice")
    _sources[source.name] = source
    return source


def _load() -> None:
    """Import every module of app.ingest, so each source has declared itself."""
    global _loaded
    if _loaded:
        return
    # Set first: a source module may read the registry while it is imported.
    _loaded = True
    package = import_module("app.ingest")
    for found in iter_modules(package.__path__):
        if not found.ispkg:
            import_module(f"app.ingest.{found.name}")


def sources() -> tuple[Source, ...]:
    """Every source type, in name order."""
    _load()
    return tuple(_sources[name] for name in sorted(_sources))


def find(name: str) -> Source | None:
    _load()
    return _sources.get(name)


def get(name: str) -> Source:
    source = find(name)
    if source is None:
        raise KeyError(name)
    return source


def collected() -> tuple[Source, ...]:
    """The sources Keen runs a collector for."""
    return tuple(source for source in sources() if source.collected)


def polled() -> tuple[Source, ...]:
    """The sources collected on the HTTP ingestion worker."""
    return tuple(source for source in sources() if source.polled)


def configured() -> tuple[Source, ...]:
    """The sources with a file of shipped collection defaults."""
    return tuple(source for source in sources() if source.config_setting)
