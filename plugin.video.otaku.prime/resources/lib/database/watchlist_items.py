# -*- coding: utf-8 -*-
"""Canonical raw watchlist boundary shared by every tracker provider."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager


SUPPORTED_WATCHLIST_PROVIDERS = ("anilist", "mal", "kitsu", "simkl")


class WatchlistItemStore:
    """Persist raw provider items until franchise + metadata placement succeeds.

    The table is intentionally provider-oriented.  A MAL row and an AniList row
    for the same anime may both exist; later identity/placement resolution can
    link them to the same Prime catalogue record without losing the provider's
    own list status/progress/raw payload.
    """

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

    def initialize(self):
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist_items(
              provider TEXT NOT NULL,
              provider_item_id TEXT NOT NULL,
              english_name TEXT,
              romaji_name TEXT,
              native_name TEXT,
              list_status TEXT NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              episode_count INTEGER,
              media_format TEXT,
              release_date TEXT,
              is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
              raw_json TEXT NOT NULL,

              relation_root_provider TEXT,
              relation_root_id TEXT,
              franchise_local_id TEXT,
              franchise_english_name TEXT,
              franchise_romaji_name TEXT,
              franchise_release_date TEXT,
              relation_path_json TEXT,
              relation_resolved INTEGER NOT NULL DEFAULT 0 CHECK(relation_resolved IN(0,1)),

              metadata_provider TEXT,
              metadata_show_id TEXT,
              placement_kind TEXT,
              metadata_season_id TEXT,
              metadata_season_number INTEGER,
              metadata_episode_id TEXT,
              metadata_episode_number INTEGER,
              placement_score INTEGER,
              placement_resolved INTEGER NOT NULL DEFAULT 0 CHECK(placement_resolved IN(0,1)),
              catalogue_season_local_id TEXT,

              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(provider,provider_item_id)
            );
            CREATE INDEX IF NOT EXISTS ix_watchlist_items_relation_pending
              ON watchlist_items(provider,relation_resolved,english_name,romaji_name);
            CREATE INDEX IF NOT EXISTS ix_watchlist_items_placement_pending
              ON watchlist_items(relation_resolved,placement_resolved,franchise_local_id);
            """)

    def replace_provider_snapshot(self, provider, entries):
        provider = str(provider or "").strip().lower()
        if provider not in SUPPORTED_WATCHLIST_PROVIDERS:
            raise ValueError("unsupported watchlist provider")
        rows_by_id = {}
        for entry in entries:
            rows_by_id[str(entry["provider_item_id"])] = entry
        rows = list(rows_by_id.values())
        ids = set()
        with self._connection() as db:
            for entry in rows:
                item_id = str(entry["provider_item_id"])
                ids.add(item_id)
                raw = entry.get("raw")
                if raw is None:
                    raw = entry
                raw_json = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                db.execute("""INSERT INTO watchlist_items(
                  provider,provider_item_id,english_name,romaji_name,native_name,
                  list_status,progress,episode_count,media_format,release_date,is_adult,raw_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(provider,provider_item_id) DO UPDATE SET
                    english_name=excluded.english_name,
                    romaji_name=excluded.romaji_name,
                    native_name=excluded.native_name,
                    list_status=excluded.list_status,
                    progress=excluded.progress,
                    episode_count=excluded.episode_count,
                    media_format=excluded.media_format,
                    release_date=excluded.release_date,
                    is_adult=excluded.is_adult,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP""", (
                    provider,item_id,entry.get("english_name"),entry.get("romaji_name"),
                    entry.get("native_name"),entry["list_status"],
                    max(0,int(entry.get("progress") or 0)),
                    int(entry["episode_count"]) if entry.get("episode_count") is not None else None,
                    entry.get("media_format"),entry.get("release_date"),
                    int(bool(entry.get("is_adult"))),raw_json,
                ))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    "DELETE FROM watchlist_items WHERE provider=? AND provider_item_id NOT IN ({})".format(placeholders),
                    (provider,) + tuple(sorted(ids)),
                )
            else:
                db.execute("DELETE FROM watchlist_items WHERE provider=?", (provider,))
        return len(rows)

    def list_provider(self, provider):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""
              SELECT * FROM watchlist_items
               WHERE provider=?
               ORDER BY LOWER(COALESCE(english_name,romaji_name,native_name,'')),provider_item_id
            """, (provider,))]

    def list_relation_pending(self, provider=None):
        sql = "SELECT * FROM watchlist_items WHERE relation_resolved=0"
        params = []
        if provider:
            sql += " AND provider=?"
            params.append(provider)
        sql += " ORDER BY LOWER(COALESCE(english_name,romaji_name,native_name,'')),provider,provider_item_id"
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def save_relation(self, provider, provider_item_id, franchise_local_id, resolution):
        with self._connection() as db:
            cursor = db.execute("""UPDATE watchlist_items SET
              relation_root_provider=?,relation_root_id=?,franchise_local_id=?,
              franchise_english_name=?,franchise_romaji_name=?,franchise_release_date=?,
              relation_path_json=?,relation_resolved=1,
              placement_resolved=0,updated_at=CURRENT_TIMESTAMP
              WHERE provider=? AND provider_item_id=?""", (
                provider,str(resolution["root_id"]),franchise_local_id,
                resolution.get("franchise_english_name"),
                resolution.get("franchise_romaji_name"),
                resolution.get("franchise_release_date"),
                json.dumps(resolution.get("relation_path") or []),
                provider,str(provider_item_id),
            ))
            if cursor.rowcount != 1:
                raise KeyError("watchlist row disappeared during franchise resolution")

    def list_placement_pending(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""
              SELECT item.*,
                     series.metadata_provider AS series_metadata_provider,
                     series.metadata_show_id,series.metadata_show_name,series.metadata_show_year
                FROM watchlist_items AS item
                JOIN tv_series AS series ON series.local_id=item.franchise_local_id
               WHERE item.relation_resolved=1 AND item.placement_resolved=0
               ORDER BY LOWER(COALESCE(item.english_name,item.romaji_name,item.native_name,'')),
                        item.provider,item.provider_item_id
            """)]

    def prepare_for_metadata_provider(self, provider):
        """Invalidate only placements made by a different metadata authority."""
        with self._connection() as db:
            db.execute("""UPDATE watchlist_items SET
              metadata_provider=NULL,metadata_show_id=NULL,placement_kind=NULL,
              metadata_season_id=NULL,metadata_season_number=NULL,
              metadata_episode_id=NULL,metadata_episode_number=NULL,
              placement_score=NULL,placement_resolved=0,catalogue_season_local_id=NULL,
              updated_at=CURRENT_TIMESTAMP
              WHERE placement_resolved=1
                AND (metadata_provider IS NULL OR metadata_provider<>?)""", (provider,))

    def save_placement(self, provider, provider_item_id, placement, catalogue_season_local_id=None):
        show_id = placement.get("show_id")
        with self._connection() as db:
            cursor = db.execute("""UPDATE watchlist_items SET
              metadata_provider=?,metadata_show_id=?,placement_kind=?,
              metadata_season_id=?,metadata_season_number=?,
              metadata_episode_id=?,metadata_episode_number=?,
              placement_score=?,placement_resolved=1,catalogue_season_local_id=?,
              updated_at=CURRENT_TIMESTAMP
              WHERE provider=? AND provider_item_id=?""", (
                placement.get("metadata_provider"),str(show_id) if show_id is not None else None,
                placement.get("kind"),
                str(placement.get("season_id")) if placement.get("season_id") is not None else None,
                int(placement["season_number"]) if placement.get("season_number") is not None else None,
                str(placement.get("episode_id")) if placement.get("episode_id") is not None else None,
                int(placement["episode_number"]) if placement.get("episode_number") is not None else None,
                int(placement.get("score") or 0),catalogue_season_local_id,
                provider,str(provider_item_id),
            ))
            if cursor.rowcount != 1:
                raise KeyError("watchlist row disappeared during metadata placement")

    def reset_placements(self):
        with self._connection() as db:
            db.execute("""UPDATE watchlist_items SET
              metadata_provider=NULL,metadata_show_id=NULL,placement_kind=NULL,
              metadata_season_id=NULL,metadata_season_number=NULL,
              metadata_episode_id=NULL,metadata_episode_number=NULL,
              placement_score=NULL,placement_resolved=0,catalogue_season_local_id=NULL,
              updated_at=CURRENT_TIMESTAMP""")
