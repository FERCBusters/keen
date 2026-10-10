import asyncio

import pytest
from app import main


@pytest.mark.parametrize(
    "failure", [None, "listener", "bootstrap", "request", "shutdown"]
)
def test_lifespan_preserves_bootstrap_and_listener_cleanup(monkeypatch, failure):
    order = []

    async def start():
        order.append("start")
        if failure == "listener":
            raise RuntimeError("Redis unavailable")

    def bootstrap():
        order.append("bootstrap")
        if failure == "bootstrap":
            raise RuntimeError("bootstrap failed")

    async def stop():
        order.append("stop")
        if failure == "shutdown":
            raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(main, "start_notification_listener", start)
    monkeypatch.setattr(main, "bootstrap_initial_admin", bootstrap)
    monkeypatch.setattr(main, "stop_notification_listener", stop)

    async def exercise():
        async with main.app.router.lifespan_context(main.app):
            order.append("serve")
            if failure == "request":
                raise RuntimeError("request failed")

    if failure in ("bootstrap", "request"):
        with pytest.raises(RuntimeError):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert order == (
        ["start", "bootstrap", "stop"]
        if failure == "bootstrap"
        else ["start", "bootstrap", "serve", "stop"]
    )
    assert not main.app.router.on_startup
    assert not main.app.router.on_shutdown
