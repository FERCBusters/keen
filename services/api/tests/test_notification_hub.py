"""Notification hub and Redis bus fanout behaviour."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.realtime import notifications as nt


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


def _quiet_hub():
    hub = MagicMock()
    hub.broadcast_admin = AsyncMock()
    hub.broadcast_user = AsyncMock()
    return hub


def test_stopping_straight_after_starting_shuts_down_cleanly():
    # Application shutdown can follow startup at once, before the listener
    # has run; stopping must not fail the shutdown.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        await bus.start(_quiet_hub())
        task = bus._task

        await bus.stop()

        assert task.done()
        assert (bus._task, bus._sub, bus._pub) == (None, None, None)

    asyncio.run(scenario())


def test_stopping_while_the_broker_is_unavailable_shuts_down_cleanly():
    async def scenario():
        reached = asyncio.Event()

        class Unavailable:
            async def subscribe(self, channel):
                reached.set()
                raise ConnectionError("broker unavailable")

            async def close(self):
                pass

        redis = MagicMock()
        redis.pubsub.return_value = Unavailable()
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        with patch.object(bus, "_sub_client", return_value=redis):
            await bus.start(_quiet_hub())
            await reached.wait()
            # The listener now waits out its retry delay.
            await asyncio.sleep(0.01)
            started = asyncio.get_running_loop().time()

            await bus.stop()

        assert asyncio.get_running_loop().time() - started < 0.5
        assert bus._task is None

    asyncio.run(scenario())


def test_a_cancelled_stop_still_reports_its_cancellation():
    # Waiting for the listener must not swallow the caller's own cancellation.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        never = asyncio.Event()

        async def listener(hub):
            try:
                await never.wait()
            except asyncio.CancelledError:
                await never.wait()

        with patch.object(bus, "_listen_loop", listener):
            await bus.start(_quiet_hub())
            await asyncio.sleep(0)
            stopping = asyncio.create_task(bus.stop())
            await asyncio.sleep(0.01)
            stopping.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stopping
            never.set()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_subscriber_client_is_created_once_with_decoded_responses():
    # The subscriber connection is created lazily, once, and decodes replies so
    # the listener can parse message payloads as text.
    bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
    with patch.object(nt.Redis, "from_url", return_value=MagicMock()) as from_url:
        first = bus._sub_client()
        assert bus._sub_client() is first
    from_url.assert_called_once_with("redis://127.0.0.1:1/0", decode_responses=True)
    assert bus._pub is None


def test_start_is_idempotent_while_the_listener_runs():
    # A second start keeps the running listener; nothing is spawned twice.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        hold = asyncio.Event()

        async def listener(hub):
            await hold.wait()

        with patch.object(bus, "_listen_loop", listener):
            await bus.start(_quiet_hub())
            first = bus._task
            await bus.start(_quiet_hub())
            assert bus._task is first
            hold.set()
            await bus.stop()
        assert bus._task is None

    asyncio.run(scenario())


def test_stop_closes_both_clients_and_survives_close_failures():
    # Shutdown closes the subscriber and publisher connections and drops them
    # even when a close call fails.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        sub = MagicMock()
        sub.close = AsyncMock(side_effect=ConnectionError("gone"))
        pub = MagicMock()
        pub.close = AsyncMock()
        bus._sub, bus._pub = sub, pub
        await bus.stop()
        sub.close.assert_awaited_once()
        pub.close.assert_awaited_once()
        assert (bus._sub, bus._pub, bus._task) == (None, None, None)

        bus._pub = MagicMock()
        bus._pub.close = AsyncMock(side_effect=RuntimeError("already closed"))
        await bus.stop()
        assert bus._pub is None

    asyncio.run(scenario())


def test_listener_ignores_non_dict_frames_and_stops_at_the_stop_signal():
    # Frames that are not dictionaries are skipped, and a message arriving
    # after the stop signal is never forwarded.
    async def scenario():
        hub = _quiet_hub()
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        admin = {"type": "message", "data": json.dumps({"scope": "admin"})}

        class PubSub:
            def __init__(self):
                self.close_calls = 0

            async def subscribe(self, channel):
                return None

            async def listen(self):
                yield "pong"
                yield None
                yield admin
                bus._stop.set()
                yield admin

            async def close(self):
                self.close_calls += 1
                raise ConnectionError("broker gone")

        pubsub = PubSub()
        redis = MagicMock()
        redis.pubsub.return_value = pubsub
        with patch.object(bus, "_sub_client", return_value=redis):
            await bus._listen_loop(hub)
        hub.broadcast_admin.assert_awaited_once_with({"scope": "admin"})
        assert pubsub.close_calls == 1

    asyncio.run(scenario())


def test_cancellation_during_a_read_ends_the_listener_quietly():
    # Cancelling the listener while it waits on the broker ends the loop
    # through its own exit path, so the task finishes without an error.
    async def scenario():
        hub = _quiet_hub()
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        reading = asyncio.Event()

        class PubSub:
            def __init__(self):
                self.closed = False

            async def subscribe(self, channel):
                return None

            async def listen(self):
                reading.set()
                await asyncio.Event().wait()
                yield {}

            async def close(self):
                self.closed = True

        pubsub = PubSub()
        redis = MagicMock()
        redis.pubsub.return_value = pubsub
        with patch.object(bus, "_sub_client", return_value=redis):
            task = asyncio.create_task(bus._listen_loop(hub))
            await reading.wait()
            task.cancel()
            await asyncio.wait({task})
        assert task.done() and not task.cancelled()
        assert task.exception() is None
        assert pubsub.closed is True
        hub.broadcast_admin.assert_not_awaited()

    asyncio.run(scenario())


def test_publisher_client_is_created_once_and_publish_failures_stay_silent():
    # The publisher connection is created lazily once; without a fallback hub
    # a failed publish of a count is absorbed so the request completes.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        client = MagicMock()
        client.publish = AsyncMock(side_effect=ConnectionError("down"))
        with patch.object(nt.Redis, "from_url", return_value=client) as from_url:
            assert bus._pub_client() is client
            assert bus._pub_client() is client
            await bus.publish_admin_open_count(1)
            await bus.publish_user_unread_replies("u1", 1)
        from_url.assert_called_once_with("redis://127.0.0.1:1/0", decode_responses=True)
        assert client.publish.await_count == 2

    asyncio.run(scenario())


def test_listener_retries_when_the_subscriber_cannot_be_created():
    # A failure before a pubsub object exists still backs off and retries.
    async def scenario():
        bus = nt.RedisNotificationBus("redis://127.0.0.1:1/0")
        attempts = {"n": 0}

        def failing_client():
            attempts["n"] += 1
            if attempts["n"] >= 2:
                bus._stop.set()
            raise ConnectionError("no broker")

        waits = []

        async def tracked_sleep(delay):
            waits.append(delay)

        with (
            patch.object(bus, "_sub_client", failing_client),
            patch.object(asyncio, "sleep", tracked_sleep),
        ):
            await bus._listen_loop(_quiet_hub())
        assert attempts["n"] == 2
        assert waits == [1.0, 2.0]

    asyncio.run(scenario())
