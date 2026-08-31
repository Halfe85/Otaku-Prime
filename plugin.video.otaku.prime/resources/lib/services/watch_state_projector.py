# -*- coding: utf-8 -*-
"""Translate canonical watchlist progress to and from episode watch states."""

from __future__ import annotations

from resources.lib.logging_config import get_logger
from resources.lib.services.watchlist_watchdog import WATCHLIST_ADDED,WATCHLIST_UPDATED


LOGGER=get_logger(__name__)


class CatalogWatchStateProjector:
    """Keep catalogue booleans as a sequential projection of tracker progress."""

    def __init__(self,catalog_store,watchlist_manager):
        self.catalog=catalog_store
        self.watchlist=watchlist_manager

    def project_item(self,item):
        if not item or not item.get("local_id"):
            return {"episode_count":0,"watched_count":0}
        result=self.catalog.project_watchlist_progress(
            item["local_id"],item.get("progress") or 0)
        if result.get("episode_count"):
            LOGGER.info(
                "Projected Prime watchlist progress for %s: %s/%s episodes watched",
                item["local_id"],result["watched_count"],result["episode_count"])
        return result

    def project_all(self,items=None):
        rows=list(items if items is not None else self.watchlist.list_items())
        episode_count=watched_count=0
        for item in rows:
            result=self.project_item(item)
            episode_count+=int(result.get("episode_count") or 0)
            watched_count+=int(result.get("watched_count") or 0)
        LOGGER.info(
            "Catalogue watch-state projection complete: items=%s watched=%s episodes=%s",
            len(rows),watched_count,episode_count)
        return {"items":len(rows),"watched_count":watched_count,
                "episode_count":episode_count}

    def handle_watchlist_event(self,event):
        if not event or event.get("type") not in (WATCHLIST_ADDED,WATCHLIST_UPDATED):
            return None
        fields=set(event.get("changed_fields") or [])
        if event.get("type")==WATCHLIST_UPDATED and fields and "progress" not in fields:
            return None
        return self.project_item(event.get("item"))

    def update_episode(self,episode_id,watched,source="library-ui"):
        if not isinstance(watched,bool):
            raise ValueError("watched must be true or false")
        context=self.catalog.episode_watch_context(episode_id)
        if not context:
            raise KeyError("episode not found")
        watchlist_id=context.get("watchlist_local_id")
        if not watchlist_id or context.get("watchlist_progress") is None:
            raise KeyError("episode is not linked to a watchlist item")
        source_number=max(1,int(context.get("source_episode_number") or 1))
        current=max(0,int(context.get("watchlist_progress") or 0))
        progress=max(current,source_number) if watched else min(current,source_number-1)
        result=self.watchlist.update_item(
            watchlist_id,progress=progress,source=source)
        # A no-op does not emit an event, so repair any stale catalogue state here.
        projection=self.project_item(result["item"]) if not result.get("changed") else None
        current_context=self.catalog.episode_watch_context(episode_id)
        return {"changed":bool(result.get("changed")),"item":result["item"],
                "episode_id":str(episode_id),"watch_status":bool(
                    current_context and current_context.get("watch_status")),
                "progress":int(result["item"].get("progress") or 0),
                "projection":projection}
