# -*- coding: utf-8 -*-
"""Main Kodi plugin entry point for Otaku Prime."""

from __future__ import annotations

import socket
import sys

import xbmcgui
import xbmcplugin

PORT = 9898


def get_server_ip() -> str:
    """Return the Kodi host's LAN IP, with a safe localhost fallback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main() -> None:
    """Show the Otaku Prime web-management address and nothing else."""
    handle = int(sys.argv[1])
    server_ip = get_server_ip()

    item = xbmcgui.ListItem(label=f"Enter http://{server_ip}:{PORT}")
    item.setProperty("IsPlayable", "false")

    xbmcplugin.addDirectoryItem(
        handle=handle,
        url="",
        listitem=item,
        isFolder=False,
    )
    xbmcplugin.endOfDirectory(handle)


if __name__ == "__main__":
    main()
