# -*- coding: utf-8 -*-
"""Main Kodi plugin entry point for Otaku Prime."""

from __future__ import annotations

import sys

import xbmc
import xbmcgui
import xbmcplugin

from service import WEB_PORT


def get_server_ip() -> str:
    """Return the IP address reported by Kodi for this device."""
    address = xbmc.getInfoLabel("Network.IPAddress").strip()
    return address or "127.0.0.1"


def main() -> None:
    """Show the Otaku Prime web-management address and nothing else."""
    handle = int(sys.argv[1])
    server_ip = get_server_ip()

    item = xbmcgui.ListItem(label=f"Enter http://{server_ip}:{WEB_PORT}")
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
