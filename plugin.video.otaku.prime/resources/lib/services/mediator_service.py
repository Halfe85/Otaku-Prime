# -*- coding: utf-8 -*-
"""Coordinate Prime media state, stream files and Kodi DB middleware."""

from __future__ import annotations


class MediatorService:
    def __init__(self, media_store, stream_library, kodi_db):
        self.media_store = media_store
        self.stream_library = stream_library
        self.kodi_db = kodi_db

    def start(self) -> dict:
        """Link existing Kodi media. Stream export is explicit until imports exist."""
        return self.kodi_db.synchronize_links()

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
        elif media_type == "movie":
            self.kodi_db.set_movie_watched(link["kodi_movie_id"], watched)
