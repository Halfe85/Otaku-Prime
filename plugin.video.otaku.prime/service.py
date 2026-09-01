# -*- coding: utf-8 -*-
"""Background service entry point for Otaku Prime."""

from __future__ import annotations

import ipaddress
import os
import socket
import threading

import xbmc
import xbmcaddon
import xbmcvfs

from resources.lib.users import UserStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.app_logs import AppLogStore
from resources.lib.database.runtime_catalog import RuntimeCatalogStore as CatalogStore
from resources.lib.services.watchlist_identity import WatchlistIdentityEnrichmentService
from resources.lib.services.watchlist_watchdog import WatchlistWatchdogStore
from resources.lib.services.watchlist_watchdog_release import (
    ReleaseAwareWatchlistWatchdogService,
)
from resources.lib.services.watchlist_provider_writer import WatchlistProviderWriter
from resources.lib.services.mediator_tvshow import TVShowMediatorService
from resources.lib.services.artwork_store import PersistentArtworkStore
from resources.lib.services.runtime_prime_physical import (
    RuntimePrimePhysicalService as PrimePhysicalService,
)
from resources.lib.services.watch_state_projector import CatalogWatchStateProjector
from resources.lib.watchlist.anilist_sync import AniListWatchlistImportService
from resources.lib.watchlist.anilist_sync import AniListWatchlistClient
from resources.lib.watchlist.provider_importers import (
    MALWatchlistClient,
    MALWatchlistImportService,
    KitsuWatchlistClient,
    KitsuWatchlistImportService,
    SimklWatchlistClient,
    SimklWatchlistImportService,
)
from resources.lib.watchlist.mal import MALAuthenticator
from resources.lib.watchlist.kitsu import KitsuAuthenticator
from resources.lib.web import create_server
from resources.lib.logging_config import configure_logging,get_logger
from resources.lib.service_lifecycle import (
    ServiceInstanceLock,
    initialize_service_stores,
    stop_service_components,
)


# Bind every IPv4 interface so the authenticated web UI is reachable from the LAN.
WEB_HOST = "0.0.0.0"
WEB_PORT = 9898
USERS_DB_NAME = "users.sqlite"
BACKGROUND_NETWORK_TIMEOUT = 3


class PrimeMonitor(xbmc.Monitor):
    pass


def _profile_path() -> str:
    addon = xbmcaddon.Addon()
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    os.makedirs(profile, exist_ok=True)
    return profile


def _network_web_urls(port: int) -> list[str]:
    """Return usable non-loopback IPv4 URLs for startup logging."""
    addresses = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(result[4][0])
    except OSError:
        pass

    # UDP connect does not send application data; it asks the routing table which
    # local address would be used and catches hosts whose hostname resolves only
    # to 127.x.x.x.
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()

    usable = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            continue
        usable.append("http://{}:{}/".format(value, int(port)))
    return sorted(set(usable))


def _run_service(profile: str) -> None:
    users_db = os.path.join(profile, USERS_DB_NAME)
    monitor = PrimeMonitor()

    user_store = UserStore(users_db)
    watchlist_items = WatchlistWatchdogStore(users_db)
    watchlist_accounts = WatchlistAccountStore(users_db)
    catalog = CatalogStore(users_db)
    app_logs = AppLogStore(users_db)
    if not initialize_service_stores(
        (user_store, watchlist_items, watchlist_accounts, catalog, app_logs),
        monitor.waitForAbort,
        lambda message: xbmc.log(message, xbmc.LOGWARNING),
    ):
        xbmc.log("OTAKU PRIME: startup cancelled while waiting for database", xbmc.LOGINFO)
        return

    def kodi_log(level,source,message):
        kodi_level={"ERROR":xbmc.LOGERROR,"WARNING":xbmc.LOGWARNING}.get(level,xbmc.LOGINFO)
        xbmc.log("OTAKU PRIME [{}] {}: {}".format(level,source,message),kodi_level)
    configure_logging(app_logs,kodi_log)

    def log(level, source, message):
        logger=get_logger(source)
        getattr(logger,{"ERROR":"error","WARNING":"warning"}.get(level,"info"))(message)

    watchlist_importers = [
        AniListWatchlistImportService(
            watchlist_accounts,watchlist_items,
            client=AniListWatchlistClient(
                timeout=BACKGROUND_NETWORK_TIMEOUT,
                halt_requested=monitor.abortRequested)),
        MALWatchlistImportService(
            watchlist_accounts,watchlist_items,
            client=MALWatchlistClient(
                timeout=BACKGROUND_NETWORK_TIMEOUT,
                halt_requested=monitor.abortRequested),
            authenticator=MALAuthenticator(timeout=BACKGROUND_NETWORK_TIMEOUT)),
        KitsuWatchlistImportService(
            watchlist_accounts,watchlist_items,
            client=KitsuWatchlistClient(
                timeout=BACKGROUND_NETWORK_TIMEOUT,
                halt_requested=monitor.abortRequested),
            authenticator=KitsuAuthenticator(timeout=BACKGROUND_NETWORK_TIMEOUT)),
        SimklWatchlistImportService(
            watchlist_accounts,watchlist_items,
            client=SimklWatchlistClient(
                timeout=BACKGROUND_NETWORK_TIMEOUT,
                halt_requested=monitor.abortRequested)),
    ]
    artwork_store=PersistentArtworkStore(
        timeout=BACKGROUND_NETWORK_TIMEOUT,
        halt_requested=monitor.abortRequested)
    artwork_store.start()
    prime_physical=PrimePhysicalService(
        catalog,halt_requested=monitor.abortRequested)
    tvshow_mediator = TVShowMediatorService(
        watchlist_items,catalog,artwork_store=artwork_store,physical=prime_physical,
        network_timeout=BACKGROUND_NETWORK_TIMEOUT,
        halt_requested=monitor.abortRequested)
    identity_enricher = WatchlistIdentityEnrichmentService(
        watchlist_items,network_timeout=BACKGROUND_NETWORK_TIMEOUT,
        halt_requested=monitor.abortRequested)
    provider_writer = WatchlistProviderWriter(
        watchlist_accounts,timeout=BACKGROUND_NETWORK_TIMEOUT,
        mal_authenticator=MALAuthenticator(timeout=BACKGROUND_NETWORK_TIMEOUT),
        kitsu_authenticator=KitsuAuthenticator(timeout=BACKGROUND_NETWORK_TIMEOUT))
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
    watch_state_projector=CatalogWatchStateProjector(catalog,watchlist_watchdog)
    watchlist_watchdog.subscribe(watch_state_projector.handle_watchlist_event)
    watch_state_projector.project_all(watchlist_items.list_all())

    try:
        server = create_server(
            WEB_HOST,
            WEB_PORT,
            user_store,
            app_logs,
            on_watchlist_changed=watchlist_watchdog.request_remote_sync,
            on_watchlist_state_changed=watchlist_watchdog.update_item,
            on_episode_watch_state_changed=watch_state_projector.update_episode,
            artwork_store=artwork_store,
            network_timeout=BACKGROUND_NETWORK_TIMEOUT,
        )
    except OSError as exc:
        log(
            "WARNING",
            "service",
            "Web server could not bind {}:{} ({}); watchlist service remains active".format(
                WEB_HOST, WEB_PORT, exc
            ),
        )
        prime_physical.project_all()
        watchlist_watchdog.start()
        monitor.waitForAbort()
        xbmc.log("OTAKU PRIME: pausing background work for addon shutdown", xbmc.LOGINFO)
        watchlist_watchdog.pause()
        configure_logging(None,kodi_log)
        watchlist_watchdog.stop(timeout=3)
        artwork_store.stop(timeout=1)
        return

    server_thread = threading.Thread(
        target=server.serve_forever,
        name="OtakuPrimeWeb",
        daemon=True,
    )
    server_thread.start()
    # Bind the admin UI first, then backfill STRM placeholders for catalogue
    # rows created before Prime Physical existed.
    prime_physical.project_all()
    watchlist_watchdog.start()
    log("INFO","service","Web service listening on all IPv4 interfaces at port {}".format(WEB_PORT))
    network_urls = _network_web_urls(WEB_PORT)
    if network_urls:
        log("INFO","service","Web UI network access: {}".format(", ".join(network_urls)))
    else:
        log("INFO","service","Web UI network access: http://<Kodi-device-LAN-IP>:{}/".format(WEB_PORT))
    log(
        "INFO",
        "watchlist-watchdog",
        "Watchlist watchdog active: provider sync, #->Z identity work, 10% mediator batches, release scheduling",
    )

    xbmc.log(
        f"OTAKU PRIME: web service listening on all IPv4 interfaces at port {WEB_PORT}",
        xbmc.LOGINFO,
    )
    for url in network_urls:
        xbmc.log("OTAKU PRIME: web UI network access: {}".format(url), xbmc.LOGINFO)
    xbmc.log(
        f"OTAKU PRIME: user database: {users_db}",
        xbmc.LOGINFO,
    )

    monitor.waitForAbort()

    # Kodi gives addon services only a short shutdown window during repository
    # updates. Halt every producer before closing the listener, and detach the
    # SQLite app-log sink so a late network response cannot reopen the database.
    xbmc.log("OTAKU PRIME: pausing background work for addon shutdown", xbmc.LOGINFO)
    configure_logging(None,kodi_log)
    shutdown=stop_service_components(
        server,
        server_thread,
        watchlist_watchdog,
        web_join_timeout=1,
        worker_timeout=3,
    )
    if shutdown["stopped"]:
        xbmc.log("OTAKU PRIME: service stopped", xbmc.LOGINFO)
    else:
        xbmc.log(
            "OTAKU PRIME: shutdown deadline reached; active components: {}".format(
                ", ".join(shutdown["active"])),xbmc.LOGWARNING)


def main() -> None:
    profile = _profile_path()
    lock_path = xbmcvfs.translatePath("special://temp/otaku-prime-service.lock")
    instance_lock = ServiceInstanceLock(lock_path)
    if not instance_lock.acquire():
        xbmc.log(
            "OTAKU PRIME: another background service instance is already active",
            xbmc.LOGWARNING,
        )
        return
    try:
        _run_service(profile)
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
