# -*- coding: utf-8 -*-
"""Shutdown ordering for the Otaku Prime background service."""

from __future__ import annotations


def stop_service_components(
    server,
    server_thread,
    watchlist_watchdog,
    *,
    web_join_timeout=1,
    worker_timeout=35,
):
    """Release the web listener before waiting for background workers.

    Kodi starts the replacement addon service as soon as an update has been
    installed.  The old service must therefore relinquish its listening socket
    first; mediator and watchlist workers may need longer to leave an in-flight
    provider request.
    """
    try:
        server.shutdown()
    finally:
        server.server_close()

    server_thread.join(timeout=web_join_timeout)
    watchlist_watchdog.stop(timeout=worker_timeout)

