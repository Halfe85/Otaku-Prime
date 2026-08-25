# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import os
import threading

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.users import UserStore
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_preferences import WatchlistPreferenceStore
from resources.lib.database.metadata_provider import MetadataProviderStore
from resources.lib.database.kodi_inventory import KodiInventoryStore
from resources.lib.database.app_logs import AppLogStore
from resources.lib.services.kodi_db_middleware import KodiDbMiddleware
from resources.lib.services.watchlist_franchise_resolver import UnifiedWatchlistFranchiseResolverService
from resources.lib.services.metadata_structure_resolver import MetadataStructureResolverService
from resources.lib.services.mediator_service import MediatorService
from resources.lib.services.startup_pipeline import StartupPipelineService
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
    media_store = WatchlistMediaStore(users_db)
    media_store.initialize()
    watchlist_items = WatchlistItemStore(users_db)
    watchlist_items.initialize()
    watchlist_accounts = WatchlistAccountStore(users_db)
    watchlist_accounts.initialize()
    watchlist_preferences = WatchlistPreferenceStore(users_db)
    watchlist_preferences.initialize()
    app_logs = AppLogStore(users_db)
    app_logs.initialize()
    metadata_store = MetadataProviderStore(users_db)
    metadata_store.initialize()
    kodi_inventory = KodiInventoryStore(users_db)
    kodi_inventory.initialize()

    def kodi_log(level,source,message):
        kodi_level={"ERROR":xbmc.LOGERROR,"WARNING":xbmc.LOGWARNING}.get(level,xbmc.LOGINFO)
        xbmc.log("OTAKU PRIME [{}] {}: {}".format(level,source,message),kodi_level)
    configure_logging(app_logs,kodi_log)

    def log(level, source, message):
        logger=get_logger(source)
        getattr(logger,{"ERROR":"error","WARNING":"warning"}.get(level,"info"))(message)

    def scraper_installed(addon_id):
        return bool(xbmc.getCondVisibility("System.HasAddon({})".format(addon_id)))

    def request_scraper_install(addon_id):
        if scraper_installed(addon_id):
            return False
        accepted = xbmcgui.Dialog().yesno(
            "Otaku Prime metadata provider",
            "Prime requires {} so Kodi uses the same season and episode numbering. "
            "Install it now?".format(addon_id),
        )
        if not accepted:
            return False
        xbmc.executebuiltin("InstallAddon({})".format(addon_id))
        return True

    metadata_resolver = MetadataStructureResolverService(
        metadata_store,
        watchlist_items,
        scraper_checker=scraper_installed,
        scraper_installer=request_scraper_install,
        media_store=media_store,
    )

    mediator = MediatorService(
        media_store,
        KodiDbMiddleware(media_store, inventory_store=kodi_inventory),
        metadata_resolver=metadata_resolver,
    )
    if metadata_resolver.is_configured():
        scraper = metadata_resolver.ensure_kodi_scraper()
        log(
            "INFO",
            "metadata",
            "Metadata resolver {} requires Kodi scraper {}; installed={}".format(
                metadata_resolver.status().get("provider"),
                scraper.get("required"),
                scraper.get("installed"),
            ),
        )
    else:
        log(
            "INFO",
            "metadata",
            "Raw watchlists and franchise relations can sync; provider placement waits for TMDB or TheTVDB configuration",
        )

    # Every connected tracker writes its provider-native records into the same
    # raw watchlist_items table. Relation and metadata placement run only after
    # all snapshots have been refreshed.
    watchlist_importers = [
        AniListWatchlistImportService(
            watchlist_accounts,
            watchlist_preferences,
            media_store,
            watchlist_store=watchlist_items,
        ),
        MALWatchlistImportService(watchlist_accounts, watchlist_items),
        KitsuWatchlistImportService(watchlist_accounts, watchlist_items),
        SimklWatchlistImportService(watchlist_accounts, watchlist_items),
    ]
    watchlist_sync = WatchlistSyncService(
        watchlist_importers,
        processors=[
            UnifiedWatchlistFranchiseResolverService(
                media_store,
                watchlist_items,
                preferences=watchlist_preferences,
                user_id=1,
            ),
            metadata_resolver,
        ],
        gate=None,
        error_handler=lambda exc: log("ERROR","watchlist","Watchlist sync failed: {}".format(exc)),
    )

    background = StartupPipelineService(
        watchlist_sync, mediator,
        result_handler=lambda name, result: log(
            "INFO",name,"Initial {} pipeline: {}".format(name,result)),
        error_handler=lambda name, exc: log(
            "ERROR",name,"Initial {} pipeline failed: {}".format(name,exc)),
    )

    try:
        server = create_server(
            WEB_HOST,
            WEB_PORT,
            user_store,
            media_store,
            app_logs,
            metadata_resolver=metadata_resolver,
            on_metadata_configured=watchlist_sync.run_once,
            on_watchlist_changed=watchlist_sync.run_once,
            kodi_inventory_store=kodi_inventory,
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
    background.start()
    log("INFO","service","Web service started on {}:{}".format(WEB_HOST,WEB_PORT))
    log("INFO","kodi-library","Using Kodi's existing video database; advancedsettings.xml is unchanged")
    log("INFO","kodi-library","Prime direct Kodi library projection enabled")

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
    background.stop()
    server.server_close()
    server_thread.join(timeout=5)

    xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
