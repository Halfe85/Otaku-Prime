# -*- coding: utf-8 -*-
"""Coordinate Prime media state, metadata authority, stream files and Kodi."""
from __future__ import annotations


class MediatorService:
    def __init__(self, media_store, stream_library, kodi_db, metadata_resolver=None):
        self.media_store = media_store
        self.stream_library = stream_library
        self.kodi_db = kodi_db
        self.metadata_resolver = metadata_resolver

    def start(self) -> dict:
        """Link existing Kodi media without opening Kodi's video DB directly."""
        result = self.kodi_db.synchronize_links()
        if self.metadata_resolver is not None:
            result["metadata"] = self.metadata_resolver.status()
        return result

    def metadata_status(self) -> dict:
        if self.metadata_resolver is None:
            return {"configured": False, "provider": None, "kodi_scraper_addon": None}
        return self.metadata_resolver.status()

    def required_kodi_scraper(self):
        if self.metadata_resolver is None:
            return None
        return self.metadata_resolver.kodi_scraper_addon()

    def publish_series(self, series: dict, episodes: list) -> list:
        paths = self.stream_library.write_series(series, episodes)
        if paths:
            self.kodi_db.scan(self.stream_library.tv_series_root)
        return paths

    def publish_tv_series(self, series: dict) -> list:
        episodes = self.media_store.list_tv_series_episodes(series["local_id"])
        return self.publish_series(series, episodes)

    def set_watch_status(self, media_type: str, local_id: str, watched: bool) -> None:
        self.media_store.set_watch_status(media_type, local_id, watched)
        link = self.media_store.get_kodi_link(media_type, local_id)
        if not link:
            return
        if media_type == "series":
            self.kodi_db.set_series_watched(link["kodi_tvshow_id"], watched)
        elif media_type == "episode":
            self.kodi_db.set_episode_watched(link["kodi_episode_id"], watched)
