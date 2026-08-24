# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import os
import threading

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.users import UserStore
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_preferences import WatchlistPreferenceStore
from resources.lib.services.kodi_db_middleware import KodiDbMiddleware
from resources.lib.services.anilist_release_schedule import AniListReleaseScheduleService
from resources.lib.services.anilist_relations import AniListFranchiseResolverService
from resources.lib.services.mediator_service import MediatorService
from resources.lib.services.release_watchdog import ReleaseWatchdogService
from resources.lib.services.stream_library import StreamLibraryService
from resources.lib.services.startup_pipeline import StartupPipelineService
from resources.lib.services.watchlist_sync import WatchlistSyncService
from resources.lib.watchlist.anilist_sync import AniListWatchlistImportService
from resources.lib.web import create_server


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


def main() -> None:
    profile = _profile_path()
    users_db = os.path.join(profile, USERS_DB_NAME)

    user_store = UserStore(users_db)
    user_store.initialize()
    media_store = WatchlistMediaStore(users_db)
    media_store.initialize()
    watchlist_accounts = WatchlistAccountStore(users_db)
    watchlist_accounts.initialize()
    watchlist_preferences = WatchlistPreferenceStore(users_db)
    watchlist_preferences.initialize()
    mediator = MediatorService(
        media_store,
        StreamLibraryService(os.path.join(profile, LIBRARY_DIR_NAME)),
        KodiDbMiddleware(media_store),
    )
    mediator.stream_library.initialize()
    release_watchdog = ReleaseWatchdogService(
        media_store,
        mediator.stream_library,
        mediator.kodi_db,
        error_handler=lambda exc: xbmc.log(
            "OTAKU PRIME: release watchdog failed: {}".format(exc),
            xbmc.LOGERROR,
        ),
        schedule_service=AniListReleaseScheduleService(media_store),
    )
    watchlist_sync = WatchlistSyncService(
        [AniListWatchlistImportService(
            watchlist_accounts, watchlist_preferences, media_store
        )],processors=[AniListFranchiseResolverService(media_store)],
        error_handler=lambda exc: xbmc.log(
            "OTAKU PRIME: watchlist sync failed: {}".format(exc), xbmc.LOGERROR
        ),
    )

    background = StartupPipelineService(
        watchlist_sync, release_watchdog, mediator,
        result_handler=lambda name, result: xbmc.log(
            "OTAKU PRIME: initial {} pipeline: {}".format(name, result), xbmc.LOGINFO
        ),
        error_handler=lambda name, exc: xbmc.log(
            "OTAKU PRIME: initial {} pipeline failed: {}".format(name, exc),
            xbmc.LOGERROR,
        ),
    )

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
    background.start()

    xbmc.log(
        f"OTAKU PRIME: web service started on {WEB_HOST}:{WEB_PORT}",
        xbmc.LOGINFO,
    )
    xbmc.log(
        f"OTAKU PRIME: user database: {users_db}",
        xbmc.LOGINFO,
    )
    xbmc.log(
        "OTAKU PRIME: configure Kodi movie source '{}' as Movies and TV source '{}' "
        "as TV shows".format(
            mediator.stream_library.movies_root,
            mediator.stream_library.tv_series_root,
        ),
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
