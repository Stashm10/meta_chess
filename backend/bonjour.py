"""Advertise the backend on the local network so the iPhone app finds it without typing an address."""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

SERVICE_TYPE = "_metachess._tcp.local."


def lan_ip() -> str | None:
    """The address other devices on the Wi-Fi use to reach this Mac (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


class Advertiser:
    def __init__(self, port: int):
        self.port = port
        self._zc = None
        self._info = None

    def start(self) -> None:
        ip = lan_ip()
        if ip is None:
            log.warning("No Wi-Fi address; the phone app must use a typed address")
            return
        try:
            from zeroconf import ServiceInfo, Zeroconf
            name = socket.gethostname().split(".")[0] or "Mac"
            self._info = ServiceInfo(
                SERVICE_TYPE,
                f"Meta Chess on {name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={"path": "/phone"},
            )
            self._zc = Zeroconf()
            # A second server (or a stale record) may hold the name: take "Meta Chess on Mac (2)".
            self._zc.register_service(self._info, allow_name_change=True)
            log.info("Advertising %s at %s:%d", SERVICE_TYPE, ip, self.port)
        except Exception:
            log.exception("Bonjour advertising failed; the phone app must use a typed address")
            self._zc = None

    def stop(self) -> None:
        if self._zc is not None:
            try:
                self._zc.unregister_service(self._info)
            finally:
                self._zc.close()
                self._zc = None
