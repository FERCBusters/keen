"""Cloud vendor posture providers, keyed by their event source."""

from __future__ import annotations

from importlib import import_module

MODULES = {
    "aws": "aws",
    "azure": "azure",
    "google_cloud": "google_cloud",
    "google_workspace": "workspace",
    "linode": "linode",
    "digitalocean": "digitalocean",
    "scaleway": "scaleway",
}
KEYS = tuple(MODULES)


def provider(key: str):
    if key not in MODULES:
        raise KeyError(key)
    return import_module("app.ingest.cloud." + MODULES[key]).PROVIDER


def providers() -> list:
    return [provider(key) for key in KEYS]
