# -*- coding: utf-8 -*-
"""Coordinate Prime media state, metadata authority, and Kodi projection."""
from __future__ import annotations


class MediatorService:
    def __init__(self, media_store, kodi_db, metadata_resolver=None):
        self.media_store = media_store
        self.kodi_db = kodi_db
        self.metadata_resolver = metadata_resolver

    def start(self) -> dict:
        """Compatibility entry point for reconciliation after inventory."""
        result = self.reconcile()
        if self.metadata_resolver is not None:
            result["metadata"] = self.metadata_resolver.status()
        return result

    def inventory(self) -> dict:
        return self.kodi_db.inventory()

    def reconcile(self) -> dict:
        return self.kodi_db.reconcile()

    def metadata_status(self) -> dict:
        if self.metadata_resolver is None:
            return {"configured": False, "provider": None, "kodi_scraper_addon": None}
        return self.metadata_resolver.status()

    def required_kodi_scraper(self):
        if self.metadata_resolver is None:
            return None
        return self.metadata_resolver.kodi_scraper_addon()

    def set_watch_status(self, media_type: str, local_id: str, watched: bool) -> None:
        self.media_store.set_watch_status(media_type, local_id, watched)
        link = self.media_store.get_kodi_link(media_type, local_id)
        if not link:
            return
        if media_type == "series":
            self.kodi_db.set_series_watched(link["kodi_tvshow_id"], watched)
        elif media_type == "episode":
            self.kodi_db.set_episode_watched(link["kodi_episode_id"], watched)
