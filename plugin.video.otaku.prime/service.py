# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import os
import threading

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.users import UserStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.app_logs import AppLogStore
from resources.lib.services.watchlist_sync import WatchlistSyncService
from resources.lib.watchlist.anilist_sync import AniListWatchlistImportService
from resources.lib.watchlist.provider_importers import (
    MALWatchlistImportService,
    KitsuWatchlistImportService,
    SimklWatchlistImportService,
)
from resources.lib.web import create_server
from resources.lib.logging_config import configure_logging,get_logger


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
    watchlist_items = WatchlistItemStore(users_db)
    watchlist_items.initialize()
    watchlist_accounts = WatchlistAccountStore(users_db)
    watchlist_accounts.initialize()
    app_logs = AppLogStore(users_db)
    app_logs.initialize()

    def kodi_log(level,source,message):
        kodi_level={"ERROR":xbmc.LOGERROR,"WARNING":xbmc.LOGWARNING}.get(level,xbmc.LOGINFO)
        xbmc.log("OTAKU PRIME [{}] {}: {}".format(level,source,message),kodi_level)
    configure_logging(app_logs,kodi_log)

    def log(level, source, message):
        logger=get_logger(source)
        getattr(logger,{"ERROR":"error","WARNING":"warning"}.get(level,"info"))(message)

    # This is the intentional application boundary: tracker-native snapshots are
    # stored in watchlist_items and are not resolved, merged, or projected.
    watchlist_importers = [
        AniListWatchlistImportService(watchlist_accounts, watchlist_items),
        MALWatchlistImportService(watchlist_accounts, watchlist_items),
        KitsuWatchlistImportService(watchlist_accounts, watchlist_items),
        SimklWatchlistImportService(watchlist_accounts, watchlist_items),
    ]
    watchlist_sync = WatchlistSyncService(
        watchlist_importers,
        watchlist_items,
        error_handler=lambda exc: log("ERROR","watchlist","Watchlist sync failed: {}".format(exc)),
    )

    try:
        server = create_server(
            WEB_HOST,
            WEB_PORT,
            user_store,
            app_logs,
            on_watchlist_changed=watchlist_sync.run_once,
        )
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
    watchlist_sync.start(run_immediately=True)
    log("INFO","service","Web service started on {}:{}".format(WEB_HOST,WEB_PORT))
    log("INFO","watchlist","Alpha9 canonical Prime watchlist synchronization is active")

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
    watchlist_sync.stop()
    server.server_close()
    server_thread.join(timeout=5)

    xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
