"""KEEN's API. Binding mosp here means every entry point that imports ``app``
(the API, workers, scripts, tests) has mosp bound before it is used."""

import mosp.host

mosp.host.bind(
    name="keen",
    title="Keen",
    config="app.core.config",
    models="app.db.models",
    db="app.db.session",
    hooks="app.mosp_hooks",
)
