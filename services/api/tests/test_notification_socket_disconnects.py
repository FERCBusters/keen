"""The notification socket when the browser has already gone.

The assembled application is driven as an ASGI server drives it. After the
browser disconnects, a server refuses the socket's closing frame, as uvicorn
does for a peer that has left; the socket still releases its connection slot
and the application finishes without an error.
"""

import asyncio

from app.realtime.notifications import notification_hub
from app.security.auth import cookie_name as session_cookie_name

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    BASE,
    keen,  # noqa: F401
)


class PeerGone(OSError):
    """What a server raises for a frame sent to a peer that has disconnected."""


def test_a_socket_whose_peer_left_releases_its_slot_when_closing_fails(
    keen,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    from app import main
    from app.db import session as database

    monkeypatch.setattr(database, "SessionLocal", main.SessionLocal)
    user = keen.user()
    sid = keen.client(user).cookies.get(session_cookie_name())
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "http_version": "1.1",
        "server": ("keen.example", 443),
        "client": ("203.0.113.9", 50000),
        "root_path": "",
        "path": "/ws/notifications",
        "raw_path": b"/ws/notifications",
        "query_string": b"",
        "headers": [
            (b"host", b"keen.example"),
            (b"origin", BASE.encode()),
            (b"cookie", f"{session_cookie_name()}={sid}".encode()),
        ],
        "subprotocols": [],
        "state": {},
    }
    incoming = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1001},
    ]
    sent = []
    slots = []

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message["type"])
        if message["type"] == "websocket.send":
            slots.append(len(notification_hub._user_sockets.get(str(user.id), ())))
        if message["type"] == "websocket.close":
            raise PeerGone("peer disconnected")

    asyncio.run(main.app(scope, receive, send))

    assert sent[0] == "websocket.accept"
    assert sent[-1] == "websocket.close"
    # The socket held one slot while open and none once it closed.
    assert slots and set(slots) == {1}
    assert str(user.id) not in notification_hub._user_sockets
