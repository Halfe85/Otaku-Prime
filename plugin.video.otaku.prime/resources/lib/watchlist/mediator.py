# -*- coding: utf-8 -*-
"""Reliable fan-out of boolean Prime watch state to connected trackers."""

from __future__ import annotations

from typing import Dict

from resources.lib.database.watchlist_media import WatchlistMediaStore


class WatchStatusMediator:
    """Drain durable watch-state updates through provider-specific adapters."""

    def __init__(self, store: WatchlistMediaStore, adapters: Dict[str, object]) -> None:
        self.store = store
        self.adapters = adapters

    def dispatch_pending(self, limit: int = 100) -> dict:
        result = {"sent": 0, "failed": 0, "unavailable": 0}
        for update in self.store.pending_watch_updates(limit):
            adapter = self.adapters.get(update["provider"])
            if adapter is None:
                self.store.fail_watch_update(update["id"], "provider adapter unavailable")
                result["unavailable"] += 1
                continue
            try:
                adapter.set_watch_status(
                    update["media_type"],
                    update["media_local_id"],
                    bool(update["watched"]),
                )
            except Exception as exc:
                self.store.fail_watch_update(update["id"], str(exc))
                result["failed"] += 1
            else:
                self.store.complete_watch_update(update["id"])
                result["sent"] += 1
        return result
