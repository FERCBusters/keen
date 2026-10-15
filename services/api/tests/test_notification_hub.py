"""Notification hub and Redis bus fanout behaviour."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.realtime import admin_notifications, notifications as nt


class FakeWebSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("dead socket")
        self.sent.append(payload)


async def collect(hub, ws, user_id="u1", admin=True):
    return await hub.connect(ws, user_id=user_id, is_admin=admin)


def test_hub_connect_disconnect_and_dead_socket_clean_up():
    async def scenario():
        hub = nt.NotificationHub()
        good = FakeWebSocket()
        dead = FakeWebSocket(fail=True)
        assert await collect(hub, good) is True
        assert await collect(hub, dead, user_id="u2") is True
        assert await collect(hub, FakeWebSocket(), admin=False) is True
        assert len(hub._admin_sockets) == 2
        assert len(hub._user_sockets) == 2
        await hub.disconnect(dead)
        assert len(hub._user_sockets) == 1
        await hub.broadcast_admin({"type": "ping"})
        assert good.sent == [{"type": "ping"}]
        assert dead.sent == []
        assert await hub.connect(good, user_id="u1", is_admin=True) is True
        await hub.broadcast_user("missing-user", {"type": "none"})
        assert good.sent == [{"type": "ping"}]
        await hub.broadcast_user("u1", {"type": "user-ping"})
        assert good.sent == [{"type": "ping"}, {"type": "user-ping"}]

    asyncio.run(scenario())


def test_hub_rejects_more_than_five_connections_per_user():
    async def scenario():
        hub = nt.NotificationHub()
        sockets = [FakeWebSocket() for _ in range(5)]
        for index, ws in enumerate(sockets):
            assert await collect(hub, ws, user_id="u1") is True
        overflow = FakeWebSocket()
        assert await collect(hub, overflow, user_id="u1") is False
        assert overflow not in hub._user_sockets["u1"]

    asyncio.run(scenario())


def test_hub_drops_failing_dispatcher_sockets():
    async def scenario():
        hub = nt.NotificationHub()
        good = FakeWebSocket()
        dead = FakeWebSocket(fail=True)
        await collect(hub, good)
        await collect(hub, dead, user_id="u2")
        await hub.broadcast_admin({"type": "fan"})
        assert good.sent == [{"type": "fan"}]
        assert "u2" not in hub._user_sockets
        assert hub._admin_sockets == {good}

    asyncio.run(scenario())


def test_bus_publish_and_fallback_behaviour():
    async def scenario():
        bus = nt.RedisNotificationBus("redis://localhost:6379/0")
        pub = MagicMock()
        pub.publish = AsyncMock()
        with patch.object(bus, "_pub_client", return_value=pub):
            await bus.publish({"type": "x"})
            pub.publish.assert_awaited_once_with(nt.CHANNEL, json.dumps({"type": "x"}))
            hub = MagicMock()
            hub.broadcast_admin = AsyncMock()
            hub.broadcast_user = AsyncMock()
            pub.publish.side_effect = RuntimeError("down")
            await bus.publish_admin_open_count(3, fallback_hub=hub)
            hub.broadcast_admin.assert_awaited_once_with(
                {"scope": "admin", "type": "questions.open_count", "open_count": 3}
            )
        # Without a fallback the failure propagates to callers.
        bus2 = nt.RedisNotificationBus("redis://localhost:6379/0")
        failing = MagicMock()
        failing.publish = AsyncMock(side_effect=RuntimeError("down"))
        with patch.object(bus2, "_pub_client", return_value=failing):
            with pytest.raises(RuntimeError):
                await bus2.publish({"type": "x"})
            fallback = MagicMock()
            fallback.broadcast_user = AsyncMock()
            await bus2.publish_user_unread_replies("u9", 2, fallback_hub=fallback)
            fallback.broadcast_user.assert_awaited_once_with(
                "u9",
                {
                    "scope": "user",
                    "type": "questions.unread_replies_count",
                    "user_id": "u9",
                    "unread_count": 2,
                },
            )

    asyncio.run(scenario())


def test_bus_start_stop_and_listen_loop_forwarding():
    async def scenario():
        hub = MagicMock()
        hub.broadcast_admin = AsyncMock()
        hub.broadcast_user = AsyncMock()
        bus = nt.RedisNotificationBus("redis://localhost:6379/0")

        message = {
            "type": "message",
            "data": json.dumps({"scope": "user", "user_id": "u1", "n": 1}),
        }

        class FakePubSub:
            def __init__(self):
                self.closed = False

            async def subscribe(self, channel):
                assert channel == nt.CHANNEL

            async def listen(self):
                yield message
                yield {"type": "subscribe"}
                yield {"type": "message", "data": "not json"}
                await bus._stop.wait()

            async def close(self):
                self.closed = True

        fake_pubsub = FakePubSub()
        redis = MagicMock()
        redis.pubsub.return_value = fake_pubsub
        with patch.object(bus, "_sub_client", return_value=redis):
            await bus.start(hub)
            task = bus._task
            await asyncio.sleep(0.05)
            # Let the listener observe the stop signal before cancelling, as
            # production awaits a network read rather than the stop event.
            bus._stop.set()
            for _ in range(20):
                await asyncio.sleep(0)
            await bus.stop()
            assert task.done() and not task.cancelled()
            assert hub.broadcast_user.await_count == 1
            assert hub.broadcast_admin.await_count == 0
            assert fake_pubsub.closed is True
            # Restarting after a completed loop must be allowed.
            await bus.start(hub)
            second = bus._task
            await asyncio.sleep(0.05)
            bus._stop.set()
            for _ in range(20):
                await asyncio.sleep(0)
            await bus.stop()
        assert second.done() and not second.cancelled()
        assert hub.broadcast_user.await_count == 2

    asyncio.run(scenario())


def test_listen_loop_refresh_after_failures_and_stop_signal():
    async def scenario():
        hub = MagicMock()
        hub.broadcast_admin = AsyncMock()
        hub.broadcast_user = AsyncMock()
        calls = {"n": 0}

        class FailingPubSub:
            async def subscribe(self, channel):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("broker unavailable")
                bus._stop.set()

            async def listen(self):
                if False:
                    yield {}

            async def close(self):
                pass

        redis = MagicMock()
        redis.pubsub.return_value = FailingPubSub()
        bus = nt.RedisNotificationBus("redis://localhost:6379/0")
        waits = []
        real_sleep = asyncio.sleep

        async def tracked_sleep(delay):
            waits.append(delay)

        with (
            patch.object(bus, "_sub_client", return_value=redis),
            patch.object(asyncio, "sleep", tracked_sleep),
        ):
            await bus._listen_loop(hub)
        assert calls["n"] == 2
        assert waits == [1.0]
        hub.broadcast_admin.assert_not_awaited()
        hub.broadcast_user.assert_not_awaited()

    asyncio.run(scenario())


def test_listen_loop_routes_admin_and_non_dict_payloads():
    async def scenario():
        hub = MagicMock()
        hub.broadcast_admin = AsyncMock()
        hub.broadcast_user = AsyncMock()
        bus = nt.RedisNotificationBus("redis://localhost:6379/0")

        class PubSub:
            def __init__(self):
                self.run = 0
                self.closed = False

            async def subscribe(self, channel):
                return None

            async def listen(self):
                self.run += 1
                if self.run > 1:
                    bus._stop.set()
                    return
                for raw in [
                    "admin-message",
                    json.dumps({}),
                    json.dumps({"scope": "user"}),
                    json.dumps({"scope": "user", "user_id": ""}),
                    None,
                    json.dumps({"scope": "admin"}),
                    json.dumps({"scope": "user", "user_id": "u1"}),
                ]:
                    yield {"type": "message", "data": raw}
                raise RuntimeError("stream lost")

            async def close(self):
                self.closed = True

        fake_pubsub = PubSub()
        redis = MagicMock()
        redis.pubsub.return_value = fake_pubsub
        with (
            patch.object(bus, "_sub_client", return_value=redis),
            patch.object(asyncio, "sleep", AsyncMock()),
        ):
            await bus._listen_loop(hub)
        # Only well-formed scoped messages with an identity are forwarded.
        hub.broadcast_admin.assert_awaited_once_with({"scope": "admin"})
        hub.broadcast_user.assert_awaited_once_with(
            "u1", {"scope": "user", "user_id": "u1"}
        )
        assert fake_pubsub.closed is True

    asyncio.run(scenario())


def test_admin_hub_proxy_uses_admin_scope():
    async def scenario():
        # Proxy methods resolve the hub through admin_notifications itself.
        with patch.object(admin_notifications, "notification_hub") as hub:
            hub.connect = AsyncMock()
            hub.disconnect = AsyncMock()
            hub.broadcast_admin = AsyncMock()
            await admin_notifications.admin_notification_hub.connect("ws")
            hub.connect.assert_awaited_once_with("ws", user_id="admin", is_admin=True)
            await admin_notifications.admin_notification_hub.disconnect("ws")
            hub.disconnect.assert_awaited_once_with("ws")
            await admin_notifications.admin_notification_hub.broadcast_open_question_count(
                4
            )
            hub.broadcast_admin.assert_awaited_once_with(
                {"type": "questions.open_count", "open_count": 4}
            )

    asyncio.run(scenario())


def test_listener_start_stop_helpers():
    async def scenario():
        with (
            patch.object(nt.notification_bus, "start", new=AsyncMock()) as start,
            patch.object(nt.notification_bus, "stop", new=AsyncMock()) as stop,
        ):
            await nt.start_notification_listener()
            start.assert_awaited_once()
            await nt.stop_notification_listener()
            stop.assert_awaited_once()

    asyncio.run(scenario())
