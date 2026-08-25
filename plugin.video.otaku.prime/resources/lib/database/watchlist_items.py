# -*- coding: utf-8 -*-
"""Canonical raw watchlist boundary shared by every tracker provider."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

SUPPORTED_WATCHLIST_PROVIDERS = ("anilist", "mal", "kitsu", "simkl")
RAW_COLUMNS = (
    "provider", "provider_item_id", "english_name", "romaji_name", "native_name",
    "list_status", "progress", "episode_count", "media_format", "release_date",
    "is_adult", "raw_json", "created_at", "updated_at",
)


class WatchlistItemStore:
    """Store provider-native snapshots without merging or resolving them."""

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

    @staticmethod
    def _create_table(db, name="watchlist_items"):
        db.execute("""CREATE TABLE IF NOT EXISTS {}(
          provider TEXT NOT NULL, provider_item_id TEXT NOT NULL,
          english_name TEXT, romaji_name TEXT, native_name TEXT,
          list_status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
          episode_count INTEGER, media_format TEXT, release_date TEXT,
          is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
          raw_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(provider,provider_item_id)
        )""".format(name))

    def initialize(self):
        with self._connection() as db:
            # These tables belonged to the removed resolver, mediator, and Kodi
            # projection pipeline. They contain derived data only; raw provider
            # snapshots and account credentials live in separate retained tables.
            for table in (
                "kodi_duplicate_candidates", "kodi_media_ownership",
                "kodi_inventory_episodes", "kodi_inventory_shows", "kodi_library_state",
                "kodi_episode_links", "kodi_movie_links", "kodi_series_links",
                "provider_watch_states", "watch_status_outbox", "provider_list_entries",
                "anilist_import_staging", "episodes", "seasons", "movies", "tv_series",
                "metadata_resolver_config",
                "watchlist_preferences",
            ):
                db.execute("DROP TABLE IF EXISTS " + table)
            self._create_table(db)
            columns = [row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")]
            if any(column not in RAW_COLUMNS for column in columns):
                db.execute("DROP TABLE IF EXISTS watchlist_items_raw_migration")
                self._create_table(db, "watchlist_items_raw_migration")
                retained = [column for column in RAW_COLUMNS if column in columns]
                names = ",".join(retained)
                db.execute("INSERT INTO watchlist_items_raw_migration({0}) SELECT {0} FROM watchlist_items".format(names))
                db.execute("DROP TABLE watchlist_items")
                db.execute("ALTER TABLE watchlist_items_raw_migration RENAME TO watchlist_items")
            db.execute("""CREATE INDEX IF NOT EXISTS ix_watchlist_items_provider_title
              ON watchlist_items(provider,english_name,romaji_name,native_name)""")

    def replace_provider_snapshot(self, provider, entries):
        provider = str(provider or "").strip().lower()
        if provider not in SUPPORTED_WATCHLIST_PROVIDERS:
            raise ValueError("unsupported watchlist provider")
        rows = list({str(entry["provider_item_id"]): entry for entry in entries}.values())
        ids = set()
        with self._connection() as db:
            for entry in rows:
                item_id = str(entry["provider_item_id"]); ids.add(item_id)
                raw = entry.get("raw") if entry.get("raw") is not None else entry
                db.execute("""INSERT INTO watchlist_items(
                  provider,provider_item_id,english_name,romaji_name,native_name,
                  list_status,progress,episode_count,media_format,release_date,is_adult,raw_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(provider,provider_item_id) DO UPDATE SET
                    english_name=excluded.english_name,romaji_name=excluded.romaji_name,
                    native_name=excluded.native_name,list_status=excluded.list_status,
                    progress=excluded.progress,episode_count=excluded.episode_count,
                    media_format=excluded.media_format,release_date=excluded.release_date,
                    is_adult=excluded.is_adult,raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP""", (
                    provider,item_id,entry.get("english_name"),entry.get("romaji_name"),
                    entry.get("native_name"),entry["list_status"],max(0,int(entry.get("progress") or 0)),
                    int(entry["episode_count"]) if entry.get("episode_count") is not None else None,
                    entry.get("media_format"),entry.get("release_date"),int(bool(entry.get("is_adult"))),
                    json.dumps(raw,ensure_ascii=False,separators=(",",":")),
                ))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute("DELETE FROM watchlist_items WHERE provider=? AND provider_item_id NOT IN ({})".format(placeholders), (provider,) + tuple(sorted(ids)))
            else:
                db.execute("DELETE FROM watchlist_items WHERE provider=?", (provider,))
        return len(rows)

    def list_provider(self, provider):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items WHERE provider=?
              ORDER BY LOWER(COALESCE(english_name,romaji_name,native_name,'')),provider_item_id""", (provider,))]

    def list_all(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT provider,provider_item_id,
              english_name,romaji_name,native_name,list_status,progress,episode_count,
              media_format,release_date,is_adult,created_at,updated_at FROM watchlist_items
              ORDER BY LOWER(COALESCE(english_name,romaji_name,native_name,'')),provider,provider_item_id""")]
