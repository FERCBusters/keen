"""KEEN's API. Binding myf here means every entry point that imports ``app``
(the API, workers, scripts, tests) has myf bound before it is used."""

import myf.host

myf.host.bind(
    name="keen",
    title="Keen",
    config="app.core.config",
    models="app.db.models",
    db="app.db.session",
    hooks="app.myf_hooks",
)
