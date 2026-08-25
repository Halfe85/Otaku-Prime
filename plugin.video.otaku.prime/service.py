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
from resources.lib.services.anilist_release_schedule import AniListReleaseScheduleService
from resources.lib.services.watchlist_franchise_resolver import UnifiedAniListFranchiseResolverService
from resources.lib.services.metadata_structure_resolver import MetadataStructureResolverService
from resources.lib.services.mediator_service import MediatorService
from resources.lib.services.release_watchdog import ReleaseWatchdogService
from resources.lib.services.stream_library import StreamLibraryService
from resources.lib.services.startup_pipeline import StartupPipelineService
from resources.lib.services.watchlist_sync import WatchlistSyncService
from resources.lib.watchlist.anilist_sync import AniListWatchlistImportService
from resources.lib.web import create_server
from resources.lib.logging_config import configure_logging,get_logger


WEB_HOST = "0.0.0.0"
WEB_PORT = 9898
USERS_DB_NAME = "users.sqlite"
LIBRARY_DIR_NAME = "library"


class PrimeMonitor(xbmc.Monitor):
    pass


def _profile_path() -> str:
    addon = xbmcaddon.Addon()
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    os.makedirs(profile, exist_ok=True)
    return profile


def _kodi_profile_path() -> str:
    profile = xbmcvfs.translatePath("special://profile/")
    os.makedirs(profile, exist_ok=True)
    return profile


def main() -> None:
    profile = _profile_path()
    kodi_profile = _kodi_profile_path()
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
        StreamLibraryService(os.path.join(kodi_profile, LIBRARY_DIR_NAME)),
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
            "Watchlist synchronization is blocked until TMDB or TheTVDB is configured",
        )

    release_watchdog = ReleaseWatchdogService(
        media_store,
        mediator.stream_library,
        mediator.kodi_db,
        error_handler=lambda exc: log("ERROR","release","Release watchdog failed: {}".format(exc)),
        schedule_service=AniListReleaseScheduleService(media_store),
        metadata_resolver=metadata_resolver,
    )
    watchlist_sync = WatchlistSyncService(
        [AniListWatchlistImportService(
            watchlist_accounts,
            watchlist_preferences,
            media_store,
            watchlist_store=watchlist_items,
        )],
        processors=[
            UnifiedAniListFranchiseResolverService(media_store, watchlist_items),
            metadata_resolver,
        ],
        gate=metadata_resolver,
        error_handler=lambda exc: log("ERROR","watchlist","Watchlist sync failed: {}".format(exc)),
    )

    background = StartupPipelineService(
        watchlist_sync, release_watchdog, mediator,
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
    log("INFO","kodi-library","Prime direct library projection enabled; .strm publication is disabled")

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
