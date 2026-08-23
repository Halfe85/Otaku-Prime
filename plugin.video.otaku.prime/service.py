# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import os
import threading

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.prime.users import UserStore
from resources.lib.prime.web import create_server


WEB_HOST = "0.0.0.0"
WEB_PORT = 9898
USERS_DB_NAME = "users.sqlite"


class PrimeMonitor(xbmc.Monitor):
    pass


def _profile_path() -> str:
    addon = xbmcaddon.Addon()
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    os.makedirs(profile, exist_ok=True)
    return profile


def main() -> None:
    profile = _profile_path()
    users_db = os.path.join(profile, USERS_DB_NAME)

    user_store = UserStore(users_db)
    user_store.initialize()

    try:
        server = create_server(WEB_HOST, WEB_PORT, user_store)
    except OSError as exc:
        xbmc.log(
            f"OTAKU PRIME: failed to bind web server on {WEB_HOST}:{WEB_PORT}: {exc}",
            xbmc.LOGERROR,
        )
        PrimeMonitor().waitForAbort()
        return

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="OtakuPrimeWeb",
        daemon=True,
    )
    server_thread.start()

    xbmc.log(
        f"OTAKU PRIME: web service started on {WEB_HOST}:{WEB_PORT}",
        xbmc.LOGINFO,
    )
    xbmc.log(
        f"OTAKU PRIME: user database: {users_db}",
        xbmc.LOGINFO,
    )

    monitor = PrimeMonitor()
    monitor.waitForAbort()

    server.shutdown()
    server.server_close()
    server_thread.join(timeout=5)

    xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
