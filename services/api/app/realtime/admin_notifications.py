"""Backwards-compatible alias for the notification hub.

Older code imports :data:`admin_notification_hub` and calls
``broadcast_open_question_count``. We use Redis pub/sub fanout and
per-user notifications in :mod:`app.realtime.notifications`.
"""

from __future__ import annotations

from app.realtime.notifications import notification_hub


class _AdminHubProxy:
    async def connect(self, ws):  # pragma: no cover
        await notification_hub.connect(ws, user_id="admin", is_admin=True)

    async def disconnect(self, ws):  # pragma: no cover
        await notification_hub.disconnect(ws)

    async def broadcast_open_question_count(self, open_count: int) -> None:
        await notification_hub.broadcast_admin(
            {"type": "questions.open_count", "open_count": int(open_count)}
        )


# Legacy name.
admin_notification_hub = _AdminHubProxy()
