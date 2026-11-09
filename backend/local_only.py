"""Keeps the server to this computer and the local network.

The server has no passwords, so two checks stop other websites from using it through the
visitor's own browser:
- Host must be a local name or address. A site that points its own domain at this computer
  (DNS rebinding) sends that domain as Host, and is refused.
- Origin, when a browser sends one, must be this server itself. A page on another site that
  posts to localhost or opens the WebSocket is refused.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def _hostname(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):                       # [::1]:8000
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_local_host(host: str) -> bool:
    name = _hostname(host)
    if not name:
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        # Single-label names (localhost, a Mac's name) and Bonjour names can't be bought on the internet.
        return "." not in name or name.endswith(".local")
    return ip.is_loopback or ip.is_private or ip.is_link_local


class LocalOnly:
    """ASGI middleware applying both checks to HTTP requests and WebSockets."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        allowed = is_local_host(host) and (origin is None or urlsplit(origin).netloc.lower() == host.lower())
        if allowed:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"Only this computer and the local network can use this server."})
