# -*- coding: utf-8 -*-
"""Persist the selected metadata resolver and provider-backed Kodi mappings."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager


SUPPORTED_PROVIDERS = ("tmdb", "thetvdb")
KODI_SCRAPER_ADDONS = {
    "tmdb": "metadata.tvshows.themoviedb.org.python",
    "thetvdb": "metadata.tvshows.thetvdb.com.v4.python",
}


class MetadataProviderStore:
    """Global metadata-provider configuration for Prime's single Kodi library."""

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
            CREATE TABLE IF NOT EXISTS metadata_resolver_config(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              provider TEXT NOT NULL CHECK(provider IN('tmdb','thetvdb')),
              auth_type TEXT NOT NULL,
              api_key TEXT,
              access_token TEXT,
              pin TEXT,
              bearer_token TEXT,
              bearer_expires_at INTEGER,
              verified_at INTEGER NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)
            self._ensure_column(db, "tv_series", "metadata_provider", "TEXT")
            self._ensure_column(db, "tv_series", "metadata_show_id", "TEXT")
            self._ensure_column(db, "tv_series", "metadata_show_name", "TEXT")
            self._ensure_column(db, "tv_series", "metadata_show_year", "INTEGER")
            self._ensure_column(db, "seasons", "metadata_provider", "TEXT")
            self._ensure_column(db, "seasons", "metadata_season_id", "TEXT")
            self._ensure_column(db, "seasons", "kodi_season_name", "TEXT")
            self._ensure_column(db, "episodes", "metadata_provider", "TEXT")
            self._ensure_column(db, "episodes", "metadata_episode_id", "TEXT")
            self._ensure_column(db, "episodes", "kodi_episode_name", "TEXT")

    @staticmethod
    def _ensure_column(db, table, column, definition):
        columns = {row[1] for row in db.execute("PRAGMA table_info({})".format(table))}
        if column not in columns:
            db.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition))

    def credentials(self):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM metadata_resolver_config WHERE singleton=1"
            ).fetchone()
            return dict(row) if row else None

    def status(self):
        row = self.credentials()
        if not row:
            return {
                "configured": False,
                "provider": None,
                "kodi_scraper_addon": None,
                "verified_at": None,
            }
        provider = row["provider"]
        return {
            "configured": bool(row.get("verified_at")),
            "provider": provider,
            "kodi_scraper_addon": KODI_SCRAPER_ADDONS.get(provider),
            "verified_at": row.get("verified_at"),
            "auth_type": row.get("auth_type"),
            "has_pin": bool(row.get("pin")),
        }

    def is_ready(self):
        return bool(self.status()["configured"])

    def save_tmdb(self, auth_type, credential, verified_at=None):
        auth_type = str(auth_type or "").strip().lower()
        credential = str(credential or "").strip()
        if auth_type not in ("bearer", "api_key"):
            raise ValueError("TMDB authentication must use a bearer token or API key")
        if not credential:
            raise ValueError("TMDB credential is required")
        verified_at = int(time.time() if verified_at is None else verified_at)
        with self._connection() as db:
            db.execute("""INSERT INTO metadata_resolver_config(
              singleton,provider,auth_type,api_key,access_token,pin,
              bearer_token,bearer_expires_at,verified_at)
              VALUES(1,'tmdb',?,?,?,NULL,NULL,NULL,?)
              ON CONFLICT(singleton) DO UPDATE SET
              provider='tmdb',auth_type=excluded.auth_type,
              api_key=excluded.api_key,access_token=excluded.access_token,
              pin=NULL,bearer_token=NULL,bearer_expires_at=NULL,
              verified_at=excluded.verified_at,updated_at=CURRENT_TIMESTAMP""", (
                auth_type,
                credential if auth_type == "api_key" else None,
                credential if auth_type == "bearer" else None,
                verified_at,
            ))

    def save_tvdb(self, api_key, pin=None, bearer_token=None, bearer_expires_at=None,
                  verified_at=None):
        api_key = str(api_key or "").strip()
        pin = str(pin or "").strip() or None
        if not api_key:
            raise ValueError("TheTVDB API key is required")
        verified_at = int(time.time() if verified_at is None else verified_at)
        with self._connection() as db:
            db.execute("""INSERT INTO metadata_resolver_config(
              singleton,provider,auth_type,api_key,access_token,pin,
              bearer_token,bearer_expires_at,verified_at)
              VALUES(1,'thetvdb','api_key',?,NULL,?,?,?,?)
              ON CONFLICT(singleton) DO UPDATE SET
              provider='thetvdb',auth_type='api_key',api_key=excluded.api_key,
              access_token=NULL,pin=excluded.pin,bearer_token=excluded.bearer_token,
              bearer_expires_at=excluded.bearer_expires_at,
              verified_at=excluded.verified_at,updated_at=CURRENT_TIMESTAMP""", (
                api_key, pin, bearer_token,
                int(bearer_expires_at) if bearer_expires_at else None,
                verified_at,
            ))

    def cache_tvdb_token(self, token, expires_at):
        with self._connection() as db:
            db.execute("""UPDATE metadata_resolver_config
              SET bearer_token=?,bearer_expires_at=?,updated_at=CURRENT_TIMESTAMP
              WHERE singleton=1 AND provider='thetvdb'""",
              (str(token), int(expires_at)))

    def invalidate_mappings(self):
        """Mark all provider-dependent Kodi mappings stale after configuration changes."""
        with self._connection() as db:
            db.execute("""UPDATE tv_series SET
              metadata_provider=NULL,metadata_show_id=NULL,
              metadata_show_name=NULL,metadata_show_year=NULL,
              updated_at=CURRENT_TIMESTAMP""")
            db.execute("""UPDATE seasons SET
              metadata_provider=NULL,metadata_season_id=NULL,kodi_season_name=NULL,
              kodi_resolved=0,updated_at=CURRENT_TIMESTAMP""")
            db.execute("""UPDATE episodes SET
              metadata_provider=NULL,metadata_episode_id=NULL,
              kodi_episode_number=NULL,kodi_episode_name=NULL,
              updated_at=CURRENT_TIMESTAMP""")

    def prepare_for_provider(self, provider):
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported metadata provider")
        with self._connection() as db:
            db.execute("""UPDATE seasons SET kodi_resolved=0
              WHERE metadata_provider IS NULL OR metadata_provider<>?""", (provider,))
            db.execute("""UPDATE episodes SET
              metadata_provider=NULL,metadata_episode_id=NULL,
              kodi_episode_number=NULL,kodi_episode_name=NULL
              WHERE metadata_provider IS NOT NULL AND metadata_provider<>?""", (provider,))

    def list_resolution_targets(self):
        provider=self.status().get("provider")
        with self._connection() as db:
            return [dict(row) for row in db.execute("""
              SELECT season.*,
                     series.english_name AS franchise_english_name,
                     series.romaji_name AS franchise_romaji_name,
                     series.anilist_root_id,
                     series.metadata_provider AS series_metadata_provider,
                     series.metadata_show_id,
                     series.metadata_show_name,
                     series.metadata_show_year,
                     (SELECT MIN(s2.release_date) FROM seasons AS s2
                       WHERE s2.related_series_id=season.related_series_id
                         AND s2.release_date IS NOT NULL) AS franchise_release_date
                FROM seasons AS season
                JOIN tv_series AS series ON series.local_id=season.related_series_id
               WHERE EXISTS(
                 SELECT 1 FROM provider_list_entries AS membership
                  WHERE membership.media_type='season'
                    AND membership.media_local_id=season.local_id)
                 AND (
                   season.kodi_resolved=0
                   OR season.metadata_provider IS NULL
                   OR season.metadata_provider<>?
                   OR EXISTS(
                     SELECT 1 FROM episodes AS episode
                      WHERE episode.related_season_id=season.local_id
                        AND (episode.metadata_provider IS NULL
                             OR episode.metadata_provider<>?
                             OR episode.metadata_episode_id IS NULL)))
               ORDER BY season.related_series_id,season.season_number
            """, (provider, provider))]

    def list_season_episodes(self, season_local_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""
              SELECT episode.*
                FROM episodes AS episode
               WHERE episode.related_season_id=?
               ORDER BY episode.episode_number
            """, (season_local_id,))]

    def apply_resolution(self, season, provider, show, provider_season, episode_mappings,
                         resolved):
        with self._connection() as db:
            db.execute("""UPDATE tv_series SET
              metadata_provider=?,metadata_show_id=?,metadata_show_name=?,
              metadata_show_year=?,updated_at=CURRENT_TIMESTAMP
              WHERE local_id=?""", (
                provider, str(show["id"]), show.get("name"), show.get("year"),
                season["related_series_id"],
            ))
            db.execute("""UPDATE seasons SET
              metadata_provider=?,metadata_season_id=?,
              kodi_show_name=?,kodi_show_year=?,kodi_season_number=?,
              kodi_season_name=?,kodi_resolved=?,updated_at=CURRENT_TIMESTAMP
              WHERE local_id=?""", (
                provider,
                str(provider_season["id"]) if provider_season.get("id") is not None else None,
                show.get("name"),
                show.get("year"),
                int(provider_season["number"]),
                provider_season.get("name"),
                int(bool(resolved)),
                season["local_id"],
            ))
            db.execute("""UPDATE episodes SET
              metadata_provider=NULL,metadata_episode_id=NULL,
              kodi_episode_number=NULL,kodi_episode_name=NULL,
              updated_at=CURRENT_TIMESTAMP
              WHERE related_season_id=?""", (season["local_id"],))
            for mapping in episode_mappings:
                db.execute("""UPDATE episodes SET
                  metadata_provider=?,metadata_episode_id=?,
                  kodi_episode_number=?,kodi_episode_name=?,
                  updated_at=CURRENT_TIMESTAMP
                  WHERE local_id=?""", (
                    provider,
                    str(mapping["provider_episode_id"]),
                    int(mapping["provider_episode_number"]),
                    mapping.get("provider_episode_name"),
                    mapping["local_id"],
                ))
