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
from resources.lib.database.franchise_catalog import FranchiseCatalogStore as CatalogStore
from resources.lib.services.watchlist_identity_simkl import SimklFirstWatchlistIdentityEnrichmentService as WatchlistIdentityEnrichmentService
from resources.lib.services.age_watchlist_store import AgePolicyWatchlistWatchdogStore
from resources.lib.services.watchlist_watchdog_release import ReleaseAwareWatchlistWatchdogService
from resources.lib.services.watchlist_provider_writer import WatchlistProviderWriter
from resources.lib.services.runtime_mediator_tvshow import RuntimeTVShowMediatorService as TVShowMediatorService
from resources.lib.services.artwork_store import PersistentArtworkStore
from resources.lib.services.runtime_prime_physical_movies import RuntimePrimePhysicalMoviesService as PrimePhysicalService
from resources.lib.services.timestamp_api import attach_timestamp_api
from resources.lib.services.watch_state_projector import CatalogWatchStateProjector
from resources.lib.watchlist.anilist_sync import AniListWatchlistImportService, AniListWatchlistClient
from resources.lib.watchlist.provider_importers import (
    MALWatchlistClient, MALWatchlistImportService,
    KitsuWatchlistClient, KitsuWatchlistImportService,
    SimklWatchlistClient, SimklWatchlistImportService,
)
from resources.lib.watchlist.mal import MALAuthenticator
from resources.lib.watchlist.kitsu import KitsuAuthenticator
from resources.lib.web import create_server
from resources.lib.logging_config import configure_logging,get_logger
from resources.lib.service_lifecycle import (
    ServiceInstanceLock,ServiceWorkHalted,initialize_service_stores,stop_service_components,
)


WEB_HOST="0.0.0.0"; WEB_PORT=9898; USERS_DB_NAME="users.sqlite"
# Kodi gives Python services roughly five seconds to stop. Background socket
# operations must therefore time out before the service shutdown deadline.
BACKGROUND_NETWORK_TIMEOUT=3; TIMESTAMP_NETWORK_TIMEOUT=3; WATCHLIST_NETWORK_TIMEOUT=3


class PrimeMonitor(xbmc.Monitor): pass


def _profile_path():
    addon=xbmcaddon.Addon(); profile=xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    os.makedirs(profile,exist_ok=True); return profile


def _network_web_urls(port):
    addresses=set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET): addresses.add(result[4][0])
    except OSError: pass
    probe=None
    try:
        probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); probe.connect(("192.0.2.1",9))
        addresses.add(probe.getsockname()[0])
    except OSError: pass
    finally:
        if probe is not None: probe.close()
    usable=[]
    for value in addresses:
        try: address=ipaddress.ip_address(value)
        except ValueError: continue
        if address.version!=4 or address.is_loopback or address.is_unspecified: continue
        usable.append("http://{}:{}/".format(value,int(port)))
    return sorted(set(usable))


def _run_service(profile):
    users_db=os.path.join(profile,USERS_DB_NAME); monitor=PrimeMonitor()
    user_store=UserStore(users_db); watchlist_items=AgePolicyWatchlistWatchdogStore(users_db)
    watchlist_accounts=WatchlistAccountStore(users_db); catalog=CatalogStore(users_db); app_logs=AppLogStore(users_db)
    if not initialize_service_stores(
        (user_store,watchlist_items,watchlist_accounts,catalog,app_logs),monitor.waitForAbort,
        lambda message:xbmc.log(message,xbmc.LOGWARNING)):
        xbmc.log("OTAKU PRIME: startup cancelled while waiting for database",xbmc.LOGINFO); return

    def kodi_log(level,source,message):
        kodi_level={"ERROR":xbmc.LOGERROR,"WARNING":xbmc.LOGWARNING}.get(level,xbmc.LOGINFO)
        xbmc.log("OTAKU PRIME [{}] {}: {}".format(level,source,message),kodi_level)
    configure_logging(app_logs,kodi_log)

    def log(level,source,message):
        logger=get_logger(source); getattr(logger,{"ERROR":"error","WARNING":"warning"}.get(level,"info"))(message)

    watchlist_importers=[
        AniListWatchlistImportService(watchlist_accounts,watchlist_items,
            client=AniListWatchlistClient(timeout=WATCHLIST_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested)),
        MALWatchlistImportService(watchlist_accounts,watchlist_items,
            client=MALWatchlistClient(timeout=WATCHLIST_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested),
            authenticator=MALAuthenticator(timeout=WATCHLIST_NETWORK_TIMEOUT)),
        KitsuWatchlistImportService(watchlist_accounts,watchlist_items,
            client=KitsuWatchlistClient(timeout=WATCHLIST_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested),
            authenticator=KitsuAuthenticator(timeout=WATCHLIST_NETWORK_TIMEOUT)),
        SimklWatchlistImportService(watchlist_accounts,watchlist_items,
            client=SimklWatchlistClient(timeout=WATCHLIST_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested)),
    ]
    artwork_store=PersistentArtworkStore(timeout=BACKGROUND_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested)
    artwork_store.start()
    prime_physical=PrimePhysicalService(catalog,artwork_store=artwork_store,halt_requested=monitor.abortRequested)

    try:
        structural_rebuild=prime_physical.rebuild_structural_catalog_if_required()
    except ServiceWorkHalted:
        log("INFO","service","Startup structural rebuild halted for Kodi shutdown")
        artwork_store.stop(timeout=1)
        return
    if structural_rebuild.get("cleanup_failed"):
        log("ERROR","structural-mediator",
            "Prime structural catalogue rebuild could not safely remove the old generated library; startup stopped")
        configure_logging(None,kodi_log); artwork_store.stop(timeout=1); return
    if structural_rebuild.get("rebuilt"):
        log("WARNING","structural-mediator",
            "Prime catalogue requeued for structural mediation: {}".format(structural_rebuild))

    tvshow_mediator=TVShowMediatorService(
        watchlist_items,catalog,artwork_store=artwork_store,physical=prime_physical,
        network_timeout=BACKGROUND_NETWORK_TIMEOUT,timestamp_timeout=TIMESTAMP_NETWORK_TIMEOUT,
        halt_requested=monitor.abortRequested)
    identity_enricher=WatchlistIdentityEnrichmentService(
        watchlist_items,network_timeout=BACKGROUND_NETWORK_TIMEOUT,halt_requested=monitor.abortRequested)
    provider_writer=WatchlistProviderWriter(
        watchlist_accounts,timeout=WATCHLIST_NETWORK_TIMEOUT,
        mal_authenticator=MALAuthenticator(timeout=WATCHLIST_NETWORK_TIMEOUT),
        kitsu_authenticator=KitsuAuthenticator(timeout=WATCHLIST_NETWORK_TIMEOUT))
    watchlist_watchdog=ReleaseAwareWatchlistWatchdogService(
        watchlist_importers,watchlist_items,provider_writer,identity_enricher=identity_enricher,
        mediator=tvshow_mediator,remote_interval_seconds=3600,release_poll_seconds=30,
        error_handler=lambda exc:log("ERROR","watchlist-watchdog","Watchlist watchdog failed: {}".format(exc)))
    identity_enricher.on_progress=watchlist_watchdog.identity_progress
    identity_enricher.on_complete=watchlist_watchdog.identity_complete
    watch_state_projector=CatalogWatchStateProjector(
        catalog,watchlist_watchdog,halt_requested=monitor.abortRequested)
    watchlist_watchdog.subscribe(watch_state_projector.handle_watchlist_event)

    def queue_timestamp_backfill():
        timestamp_mediator=getattr(tvshow_mediator,"timestamp_mediator",None)
        if timestamp_mediator is None: return {"items":0,"episodes":0}
        items=episodes=0
        for item in watchlist_items.list_all():
            if monitor.abortRequested(): break
            if not int(item.get("added_to_library") or 0): continue
            result=timestamp_mediator.schedule_watchlist_item(item); count=int(result.get("scheduled") or 0)
            if count: items+=1; episodes+=count
        log("INFO","services-mediator_timestamp",
            "Timestamp mediator startup backfill queued: items={} episodes={}".format(items,episodes))
        return {"items":items,"episodes":episodes}

    server=None; server_thread=None
    try:
        server=create_server(
            WEB_HOST,WEB_PORT,user_store,app_logs,
            on_watchlist_changed=watchlist_watchdog.request_remote_sync,
            on_watchlist_state_changed=watchlist_watchdog.update_item,
            on_episode_watch_state_changed=watch_state_projector.update_episode,
            artwork_store=artwork_store,network_timeout=BACKGROUND_NETWORK_TIMEOUT)
        attach_timestamp_api(server,catalog,on_age_policy_changed=prime_physical.reconcile_age_policy)
    except OSError as exc:
        if server is not None:
            try: server.server_close()
            except Exception: pass
            server=None
        log("WARNING","service",
            "Web server setup failed on {}:{} ({}); watchlist service remains active".format(WEB_HOST,WEB_PORT,exc))
    except Exception:
        if server is not None:
            try: server.server_close()
            except Exception: pass
        artwork_store.stop(timeout=1)
        log("ERROR","service","Web server setup failed unexpectedly; startup resources were stopped")
        raise
    if server:
        server_thread=threading.Thread(target=server.serve_forever,name="OtakuPrimeWeb",daemon=True)
        server_thread.start()

    try:
        log("INFO","service","Startup watch-state projection beginning")
        watch_state_projector.project_all(watchlist_items.list_all())
        log("INFO","service","Startup physical projection beginning")
        prime_physical.project_all()
        if monitor.abortRequested():
            raise ServiceWorkHalted("startup projection completed after shutdown request")
        queue_timestamp_backfill()
        if monitor.abortRequested():
            raise ServiceWorkHalted("timestamp backfill completed after shutdown request")
        watchlist_watchdog.start()
        if server:
            log("INFO","service","Web service listening on all IPv4 interfaces at port {}".format(WEB_PORT))
            network_urls=_network_web_urls(WEB_PORT)
            if network_urls: log("INFO","service","Web UI network access: {}".format(", ".join(network_urls)))
            else: log("INFO","service","Web UI network access: http://<Kodi-device-LAN-IP>:{}/".format(WEB_PORT))
            xbmc.log("OTAKU PRIME: web service listening on all IPv4 interfaces at port {}".format(WEB_PORT),xbmc.LOGINFO)
            for url in network_urls: xbmc.log("OTAKU PRIME: web UI network access: {}".format(url),xbmc.LOGINFO)
        log("INFO","watchlist-watchdog",
            "Watchlist watchdog active: provider sync, #->Z identity work, 10% mediator batches, release scheduling")
        xbmc.log("OTAKU PRIME: user database: {}".format(users_db),xbmc.LOGINFO)
        monitor.waitForAbort()
    except ServiceWorkHalted as exc:
        log("INFO","service","Startup/background work halted cleanly: {}".format(exc))
    finally:
        xbmc.log("OTAKU PRIME: pausing background work for addon shutdown",xbmc.LOGINFO)
        configure_logging(None,kodi_log)
        get_logger("shutdown").info(
            "Shutdown thread snapshot before cleanup: %s",
            [{"name":thread.name,"daemon":thread.daemon,"alive":thread.is_alive()}
             for thread in threading.enumerate()])

        def shutdown_event(component,action,facts):
            get_logger("shutdown").info(
                "Shutdown component=%s action=%s facts=%s",component,action,facts)

        shutdown=stop_service_components(
            server,server_thread,watchlist_watchdog,physical=prime_physical,
            artwork_store=artwork_store,web_join_timeout=1,worker_timeout=3,
            on_event=shutdown_event)
        if shutdown["stopped"]:
            xbmc.log("OTAKU PRIME: service stopped in {}s".format(shutdown["elapsed"]),xbmc.LOGINFO)
        else:
            xbmc.log("OTAKU PRIME: shutdown deadline reached; active components: {}".format(
                ", ".join(shutdown["active"])),xbmc.LOGWARNING)


def main():
    profile=_profile_path(); lock_path=xbmcvfs.translatePath("special://temp/otaku-prime-service.lock")
    instance_lock=ServiceInstanceLock(lock_path)
    if not instance_lock.acquire():
        xbmc.log("OTAKU PRIME: another background service instance is already active",xbmc.LOGWARNING); return
    try: _run_service(profile)
    finally: instance_lock.release()


if __name__=="__main__": main()
