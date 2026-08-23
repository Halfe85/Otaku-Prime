# -*- coding: utf-8 -*-
"""Main Kodi plugin entry point for Otaku Prime."""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional

import xbmc
import xbmcgui

from service import WEB_PORT


KODI_IP_RETRIES = 8
KODI_IP_RETRY_DELAY_MS = 250


def _usable_ip(address: str) -> Optional[str]:
    """Return a usable IPv4 address, rejecting Kodi status text."""
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return None

    if parsed.version != 4 or parsed.is_unspecified:
        return None
    return str(parsed)


def _network_ip() -> Optional[str]:
    """Ask the OS which local address it would use for LAN traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect chooses a local interface without sending any packets.
        sock.connect(("192.0.2.1", 9))
        return _usable_ip(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def get_server_ip() -> str:
    """Return Kodi's local IPv4 address, with an OS-level fallback."""
    for attempt in range(KODI_IP_RETRIES):
        address = _usable_ip(xbmc.getInfoLabel("Network.IPAddress"))
        if address:
            return address
        if attempt < KODI_IP_RETRIES - 1:
            xbmc.sleep(KODI_IP_RETRY_DELAY_MS)

    return _network_ip() or "127.0.0.1"


def main() -> None:
    """Show the Otaku Prime web-management address in a modal dialog."""
    server_ip = get_server_ip()
    xbmcgui.Dialog().ok(
        "Otaku Prime",
        "Open Otaku Prime in your browser:\n\n"
        f"http://{server_ip}:{WEB_PORT}\n\n"
        f"Service port: {WEB_PORT}",
    )


if __name__ == "__main__":
    main()
