# -*- coding: utf-8 -*-
"""Persist AniList relation resolution on watchlist staging rows only."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager


class WatchlistRelationStore:
    """Keep relation discovery separate from Prime's promoted media catalogue."""

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
            self._ensure_column(db, "anilist_import_staging", "relation_root_id", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "franchise_local_id", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "franchise_english_name", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "franchise_romaji_name", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "franchise_release_date", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "relation_type", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "media_category", "TEXT")
            self._ensure_column(db, "anilist_import_staging", "relation_path_json", "TEXT")
            self._ensure_column(
                db, "anilist_import_staging", "relation_resolved",
                "INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _ensure_column(db, table, column, definition):
        columns = {row[1] for row in db.execute(
            "PRAGMA table_info({})".format(table)
        )}
        if column not in columns:
            db.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition)
            )

    def save_resolution(self, anilist_id, franchise_local_id, resolution):
        with self._connection() as db:
            cursor = db.execute(
                """UPDATE anilist_import_staging SET
                   relation_root_id=?,franchise_local_id=?,
                   franchise_english_name=?,franchise_romaji_name=?,
                   franchise_release_date=?,relation_type=?,media_category=?,
                   relation_path_json=?,relation_resolved=1,
                   synced_at=CURRENT_TIMESTAMP
                   WHERE anilist_id=?""",
                (
                    str(resolution["root_id"]),
                    franchise_local_id,
                    resolution.get("franchise_english_name"),
                    resolution.get("franchise_romaji_name"),
                    resolution.get("franchise_release_date"),
                    resolution.get("relation_type"),
                    resolution.get("media_category"),
                    json.dumps(resolution.get("relation_path") or []),
                    str(anilist_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("AniList watchlist row disappeared during relation resolution")

    def list_resolved(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                """SELECT staging.*,
                          series.metadata_provider AS series_metadata_provider,
                          series.metadata_show_id,
                          series.metadata_show_name,
                          series.metadata_show_year
                     FROM anilist_import_staging AS staging
                     JOIN tv_series AS series
                       ON series.local_id=staging.franchise_local_id
                    WHERE staging.relation_resolved=1
                      AND NOT EXISTS(
                        SELECT 1 FROM seasons AS season
                         WHERE season.anilist_id=staging.anilist_id
                           AND season.kodi_resolved=1)
                    ORDER BY staging.synced_at,staging.anilist_id"""
            )]

    def get(self, anilist_id):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM anilist_import_staging WHERE anilist_id=?",
                (str(anilist_id),),
            ).fetchone()
            return dict(row) if row else None
