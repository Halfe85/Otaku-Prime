# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import os
import threading

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.users import UserStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.app_logs import AppLogStore
from resources.lib.database.catalog import CatalogStore
from resources.lib.services.watchlist_identity import WatchlistIdentityEnrichmentService
from resources.lib.services.watchlist_watchdog import WatchlistWatchdogStore
from resources.lib.services.watchlist_watchdog_release import (
    ReleaseAwareWatchlistWatchdogService,
)
from resources.lib.services.watchlist_provider_writer import WatchlistProviderWriter
from resources.lib.services.mediator_tvshow import TVShowMediatorService
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
    watchlist_items = WatchlistWatchdogStore(users_db)
    watchlist_items.initialize()
    watchlist_accounts = WatchlistAccountStore(users_db)
    watchlist_accounts.initialize()
    catalog = CatalogStore(users_db)
    catalog.initialize()
    app_logs = AppLogStore(users_db)
    app_logs.initialize()

    def kodi_log(level,source,message):
        kodi_level={"ERROR":xbmc.LOGERROR,"WARNING":xbmc.LOGWARNING}.get(level,xbmc.LOGINFO)
        xbmc.log("OTAKU PRIME [{}] {}: {}".format(level,source,message),kodi_level)
    configure_logging(app_logs,kodi_log)

    def log(level, source, message):
        logger=get_logger(source)
        getattr(logger,{"ERROR":"error","WARNING":"warning"}.get(level,"info"))(message)

    watchlist_importers = [
        AniListWatchlistImportService(watchlist_accounts, watchlist_items),
        MALWatchlistImportService(watchlist_accounts, watchlist_items),
        KitsuWatchlistImportService(watchlist_accounts, watchlist_items),
        SimklWatchlistImportService(watchlist_accounts, watchlist_items),
    ]
    tvshow_mediator = TVShowMediatorService(watchlist_items,catalog)
    identity_enricher = WatchlistIdentityEnrichmentService(watchlist_items)
    provider_writer = WatchlistProviderWriter(watchlist_accounts)
    watchlist_watchdog = ReleaseAwareWatchlistWatchdogService(
        watchlist_importers,
        watchlist_items,
        provider_writer,
        identity_enricher=identity_enricher,
        mediator=tvshow_mediator,
        remote_interval_seconds=3600,
        release_poll_seconds=30,
        error_handler=lambda exc: log(
            "ERROR", "watchlist-watchdog", "Watchlist watchdog failed: {}".format(exc)
        ),
    )
    identity_enricher.on_progress = watchlist_watchdog.identity_progress
    identity_enricher.on_complete = watchlist_watchdog.identity_complete

    try:
        server = create_server(
            WEB_HOST,
            WEB_PORT,
            user_store,
            app_logs,
            on_watchlist_changed=watchlist_watchdog.request_remote_sync,
            on_watchlist_state_changed=watchlist_watchdog.update_item,
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
    watchlist_watchdog.start()
    log("INFO","service","Web service started on {}:{}".format(WEB_HOST,WEB_PORT))
    log(
        "INFO",
        "watchlist-watchdog",
        "Watchlist watchdog active: provider sync, #->Z identity work, 10% mediator batches, release scheduling",
    )

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
    watchlist_watchdog.stop()
    tvshow_mediator.stop()
    server.server_close()
    server_thread.join(timeout=5)

    xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
