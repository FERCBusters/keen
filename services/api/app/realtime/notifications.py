from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket
from redis.asyncio import Redis

from app.core.config import settings

CHANNEL = "keen:notifications"


class NotificationHub:
    """Process-local WebSocket fanout.

    This hub is intentionally in-memory; cross-process fanout is done via
    Redis/Valkey pub/sub (see RedisNotificationBus).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._admin_sockets: set[WebSocket] = set()
        self._user_sockets: dict[str, set[WebSocket]] = {}

    async def connect(self, ws: WebSocket, *, user_id: str, is_admin: bool) -> None:
        async with self._lock:
            if is_admin:
                self._admin_sockets.add(ws)
            self._user_sockets.setdefault(user_id, set()).add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._admin_sockets.discard(ws)
            # Remove from any user buckets.
            dead_users: list[str] = []
            for uid, socks in self._user_sockets.items():
                socks.discard(ws)
                if not socks:
                    dead_users.append(uid)
            for uid in dead_users:
                self._user_sockets.pop(uid, None)

    async def _broadcast(
        self, targets: list[WebSocket], payload: dict[str, Any]
    ) -> None:
        if not targets:
            return

        async def _send_one(sock: WebSocket) -> None:
            try:
                await sock.send_json(payload)
            except Exception:
                # Drop dead sockets.
                await self.disconnect(sock)

        await asyncio.gather(*(_send_one(s) for s in targets), return_exceptions=True)

    async def broadcast_admin(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._admin_sockets)
        await self._broadcast(targets, payload)

    async def broadcast_user(self, user_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._user_sockets.get(user_id, set()))
        await self._broadcast(targets, payload)


class RedisNotificationBus:
    """Cross-process notification fanout via Redis/Valkey pub/sub."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pub: Redis | None = None
        self._sub: Redis | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def _pub_client(self) -> Redis:
        if self._pub is None:
            self._pub = Redis.from_url(self._redis_url, decode_responses=True)
        return self._pub

    def _sub_client(self) -> Redis:
        if self._sub is None:
            self._sub = Redis.from_url(self._redis_url, decode_responses=True)
        return self._sub

    async def start(self, hub: NotificationHub) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._listen_loop(hub))

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._task = None

        try:
            if self._sub is not None:
                await self._sub.close()
        except Exception:
            pass
        self._sub = None

        try:
            if self._pub is not None:
                await self._pub.close()
        except Exception:
            pass
        self._pub = None

    async def publish(self, payload: dict[str, Any]) -> None:
        """Best-effort publish."""
        try:
            await self._pub_client().publish(CHANNEL, json.dumps(payload))
        except Exception:
            # Allow caller to fallback to local fanout.
            raise

    async def publish_admin_open_count(
        self, open_count: int, *, fallback_hub: NotificationHub | None = None
    ) -> None:
        payload = {
            "scope": "admin",
            "type": "questions.open_count",
            "open_count": int(open_count),
        }
        try:
            await self.publish(payload)
        except Exception:
            if fallback_hub is not None:
                await fallback_hub.broadcast_admin(payload)

    async def publish_user_unread_replies(
        self,
        user_id: str,
        unread_count: int,
        *,
        fallback_hub: NotificationHub | None = None,
    ) -> None:
        payload = {
            "scope": "user",
            "type": "questions.unread_replies_count",
            "user_id": str(user_id),
            "unread_count": int(unread_count),
        }
        try:
            await self.publish(payload)
        except Exception:
            if fallback_hub is not None:
                await fallback_hub.broadcast_user(str(user_id), payload)

    async def _listen_loop(self, hub: NotificationHub) -> None:
        """Subscribe and forward messages to in-process WebSocket clients."""
        backoff = 1.0

        while not self._stop.is_set():
            pubsub = None
            try:
                pubsub = self._sub_client().pubsub()
                await pubsub.subscribe(CHANNEL)
                backoff = 1.0

                async for msg in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("type") != "message":
                        continue

                    raw = msg.get("data")
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue

                    scope = str(payload.get("scope") or "")
                    if scope == "admin":
                        await hub.broadcast_admin(payload)
                    elif scope == "user":
                        uid = str(payload.get("user_id") or "")
                        if uid:
                            await hub.broadcast_user(uid, payload)
            except asyncio.CancelledError:
                break
            except Exception:
                # Retry loop after a backoff.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                try:
                    if pubsub is not None:
                        await pubsub.close()
                except Exception:
                    pass


# Singletons used throughout the API.
notification_hub = NotificationHub()
notification_bus = RedisNotificationBus(settings.redis_url)


async def start_notification_listener() -> None:
    await notification_bus.start(notification_hub)


async def stop_notification_listener() -> None:
    await notification_bus.stop()
