"""Views of the source registry in the shapes the tests compare against."""

from app.ingest import registry
from app.services.ingestion_pause import pausable_sources

HTTP_INGEST_SOURCES = tuple(source.name for source in registry.polled())
SOURCE_RUN_SECONDS = {source.name: source.run_seconds for source in registry.polled()}
POLLING_SOURCES = frozenset(source.name for source in registry.collected())
PAUSABLE_SOURCES = pausable_sources()
FIELDS = {
    source.name: (" ".join(source.public_fields), " ".join(source.secret_fields))
    for source in registry.sources()
}
INPUT_SECTIONS = {source.name: source.input_sections for source in registry.polled()}
CONNECTORS = {
    source.name: (source.config_setting, source.section, source.id_field)
    for source in registry.configured()
}
