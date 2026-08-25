# -*- coding: utf-8 -*-
"""Persist TMDB/TheTVDB-owned catalogue structure without clearing sibling mappings."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager


class ProviderStructureStore:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def apply(self, franchise_local_id, season_local_id, provider, show,
              provider_season, episode_mappings):
        """Apply one provider season/partial-special placement.

        Unlike the legacy metadata resolver this method never clears every
        episode mapping in a season before writing the current item.  That is
        essential for a shared provider Season 0 where multiple watchlist rows
        can map to different special episodes over time.
        """
        with self._connection() as db:
            db.execute("""UPDATE tv_series SET
              metadata_provider=?,metadata_show_id=?,metadata_show_name=?,
              metadata_show_year=?,updated_at=CURRENT_TIMESTAMP
              WHERE local_id=?""", (
                provider,str(show["id"]),show.get("name"),show.get("year"),
                franchise_local_id,
            ))
            db.execute("""UPDATE seasons SET
              metadata_provider=?,metadata_season_id=?,
              kodi_show_name=?,kodi_show_year=?,kodi_season_number=?,
              kodi_season_name=?,kodi_resolved=1,updated_at=CURRENT_TIMESTAMP
              WHERE local_id=?""", (
                provider,
                str(provider_season.get("id")) if provider_season.get("id") is not None else None,
                show.get("name"),show.get("year"),int(provider_season["number"]),
                provider_season.get("name"),season_local_id,
            ))
            for mapping in episode_mappings:
                db.execute("""UPDATE episodes SET
                  metadata_provider=?,metadata_episode_id=?,
                  kodi_episode_number=?,kodi_episode_name=?,
                  updated_at=CURRENT_TIMESTAMP
                  WHERE local_id=?""", (
                    provider,str(mapping["provider_episode_id"]),
                    int(mapping["provider_episode_number"]),
                    mapping.get("provider_episode_name"),mapping["local_id"],
                ))
