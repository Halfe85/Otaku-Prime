# -*- coding: utf-8 -*-
"""Main Kodi plugin entry point for Otaku Prime."""

from __future__ import annotations

import os
import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.prime.users import UserStore
from service import USERS_DB_NAME, WEB_PORT


def get_server_ip() -> str:
    """Return the IP address reported by Kodi for this device."""
    address = xbmc.getInfoLabel("Network.IPAddress").strip()
    return address or "127.0.0.1"


def get_user_store() -> UserStore:
    """Open the same web-user database owned by service.py."""
    addon = xbmcaddon.Addon()
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    os.makedirs(profile, exist_ok=True)

    store = UserStore(os.path.join(profile, USERS_DB_NAME))
    store.initialize()
    return store


def _add_info_item(handle: int, label: str) -> None:
    item = xbmcgui.ListItem(label=label)
    item.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(
        handle=handle,
        url="",
        listitem=item,
        isFolder=False,
    )


def main() -> None:
    """Show the Otaku Prime web-management address and login information."""
    handle = int(sys.argv[1])
    server_ip = get_server_ip()
    store = get_user_store()

    _add_info_item(handle, f"Enter http://{server_ip}:{WEB_PORT}")

    default_admin = store.authenticate("admin", "admin")
    if default_admin and default_admin.get("must_change_password"):
        _add_info_item(handle, "Username: admin")
        _add_info_item(handle, "Password: admin")
        _add_info_item(handle, "Change the default password after signing in")
    else:
        _add_info_item(handle, "Login with your configured Otaku Prime account")

    xbmcplugin.endOfDirectory(handle)


if __name__ == "__main__":
    main()
