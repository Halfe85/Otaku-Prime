# -*- coding: utf-8 -*-
"""Runtime catalogue policies for shared seasons and timestamp metadata."""
from __future__ import annotations

import re
import time

from resources.lib.database.catalog import CatalogStore
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
TIMESTAMP_TYPES = ("intro", "recap", "credits", "preview")
TIMESTAMP_TTL = {
    "FOUND": 30 * 24 * 60 * 60,
    "EMPTY": 7 * 24 * 60 * 60,
    "ERROR": 60 * 60,
}
SPECIAL_LOCATOR_RE = re.compile(r"^S(\d{1,3})E(\d{1,4})$", re.IGNORECASE)


class RuntimeCatalogStore(CatalogStore):
    """CatalogStore with stable episode allocation and skip-segment metadata."""

    def initialize(self):
        super().initialize()
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS episode_segments(
              local_id INTEGER PRIMARY KEY AUTOINCREMENT,
              episode_local_id TEXT NOT NULL,
              segment_type TEXT NOT NULL
                CHECK(segment_type IN('intro','recap','credits','preview')),
              segment_index INTEGER NOT NULL DEFAULT 0 CHECK(segment_index>=0),
              start_ms INTEGER NOT NULL CHECK(start_ms>=0),
              end_ms INTEGER CHECK(end_ms IS NULL OR end_ms>=0),
              source TEXT NOT NULL,
              source_duration_ms INTEGER,
              source_ref TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(episode_local_id,segment_type,segment_index),
              FOREIGN KEY(episode_local_id) REFERENCES episodes(local_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_episode_segments_episode
              ON episode_segments(episode_local_id,segment_type,segment_index);

            CREATE TABLE IF NOT EXISTS episode_timestamp_state(
              episode_local_id TEXT PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'EMPTY'
                CHECK(status IN('FOUND','EMPTY','ERROR')),
              checked_epoch INTEGER NOT NULL DEFAULT 0,
              segment_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(episode_local_id) REFERENCES episodes(local_id) ON DELETE CASCADE
            );
            """)
            season_ids = [str(row[0]) for row in db.execute(
                "SELECT local_id FROM seasons WHERE season_number=0"
            ).fetchall()]
        for season_id in season_ids:
            self._resequence_specials(season_id)

    def _resequence_specials(self, season_id):
        """Give shared S00 entries deterministic release-order coordinates."""
        with self._connection() as db:
            season = db.execute(
                "SELECT season_number FROM seasons WHERE local_id=?",
                (str(season_id),),
            ).fetchone()
            if (not season or season["season_number"] is None or
                    int(season["season_number"]) != 0):
                return 0
            rows = db.execute("""SELECT episodes.local_id,episodes.episode_number,
              episodes.source_episode_number,episodes.watchlist_local_id,
              COALESCE(NULLIF(watchlist_items.release_date,''),
                       NULLIF(episodes.release_date,''),'9999-12-31') AS sort_date
              FROM episodes LEFT JOIN watchlist_items
                ON watchlist_items.local_id=episodes.watchlist_local_id
              WHERE episodes.related_season_id=?
              ORDER BY sort_date,
                CASE WHEN episodes.watchlist_local_id IS NULL THEN 1 ELSE 0 END,
                episodes.watchlist_local_id,episodes.source_episode_number,
                episodes.local_id""", (str(season_id),)).fetchall()
            moves = [(row, index) for index, row in enumerate(rows, 1)
                     if int(row["episode_number"]) != index]
            if not moves:
                if rows:
                    db.execute("UPDATE seasons SET first_episode=1,last_episode=?,"
                               "updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
                               (len(rows), str(season_id)))
                return 0

            temporary_base = max(
                [int(row["episode_number"]) for row in rows] + [0]
            ) + len(rows) + 1
            for index, row in enumerate(rows, 1):
                db.execute("UPDATE episodes SET episode_number=? WHERE local_id=?",
                           (temporary_base + index, row["local_id"]))
            for index, row in enumerate(rows, 1):
                db.execute("""UPDATE episodes SET episode_number=?,
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (index, row["local_id"]))
            db.execute("""UPDATE seasons SET first_episode=1,last_episode=?,
              updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (len(rows), str(season_id)))
        LOGGER.info(
            "Shared Prime specials resequenced by release date: season=%s episodes=%s moved=%s",
            season_id, len(rows), len(moves),
        )
        return len(moves)

    def _runtime_episode_number(self, season_id, episode_number,
                                source_episode_number=None,
                                watchlist_local_id=None):
        requested = int(episode_number)
        source_number = int(source_episode_number or requested)
        watchlist_id = (
            str(watchlist_local_id)
            if watchlist_local_id not in (None, "") else None
        )
        if not watchlist_id:
            return requested

        with self._connection() as db:
            season = db.execute(
                "SELECT season_number FROM seasons WHERE local_id=?",
                (str(season_id),),
            ).fetchone()
            if not season:
                raise KeyError("season not found")

            existing = db.execute("""SELECT episode_number FROM episodes
              WHERE related_season_id=? AND watchlist_local_id=?
                AND source_episode_number=?""",
                (str(season_id), watchlist_id, source_number),
            ).fetchone()
            if existing:
                return int(existing["episode_number"])

            candidate = db.execute("""SELECT watchlist_local_id,source_episode_number
              FROM episodes WHERE related_season_id=? AND episode_number=?""",
                (str(season_id), requested),
            ).fetchone()
            if not candidate or candidate["watchlist_local_id"] is None:
                return requested

            allocated = int(db.execute("""SELECT COALESCE(MAX(episode_number),0)+1
              FROM episodes WHERE related_season_id=?""",
                (str(season_id),),
            ).fetchone()[0])
            log = LOGGER.info if int(season["season_number"] or 0) == 0 else LOGGER.warning
            log(
                "Prime season coordinate collision: S%02dE%02d is owned by watchlist "
                "item %s; temporarily mapped watchlist item %s source episode %s to E%02d",
                int(season["season_number"] or 0), requested,
                candidate["watchlist_local_id"], watchlist_id, source_number, allocated,
            )
            return allocated

    def add_episode(self, season_id, episode_number, source_episode_number=None,
                    mal_id=None, simkl_id=None, anilist_id=None, kitsu_id=None,
                    watch_status=None, release_date=None, title=None, overview=None,
                    runtime_minutes=None, watchlist_local_id=None):
        stable_number = self._runtime_episode_number(
            season_id,
            episode_number,
            source_episode_number=source_episode_number,
            watchlist_local_id=watchlist_local_id,
        )
        stored = super().add_episode(
            season_id,
            stable_number,
            source_episode_number=source_episode_number,
            mal_id=mal_id,
            simkl_id=simkl_id,
            anilist_id=anilist_id,
            kitsu_id=kitsu_id,
            watch_status=watch_status,
            release_date=release_date,
            title=title,
            overview=overview,
            runtime_minutes=runtime_minutes,
            watchlist_local_id=watchlist_local_id,
        )
        self._resequence_specials(season_id)
        with self._connection() as db:
            refreshed = db.execute(
                "SELECT * FROM episodes WHERE local_id=?", (stored["local_id"],)
            ).fetchone()
            return dict(refreshed) if refreshed else stored

    # Timestamp metadata -------------------------------------------------
    @staticmethod
    def _timestamp_coordinate(season_number, episode_number, special_locator=None):
        locator = str(special_locator or "").strip()
        match = SPECIAL_LOCATOR_RE.match(locator)
        if match:
            return int(match.group(1)), int(match.group(2))
        if season_number is None or episode_number is None:
            return None, None
        return int(season_number), int(episode_number)

    def timestamp_contexts_for_watchlist(self, watchlist_local_id, series_id=None,
                                         force=False, now_epoch=None):
        """Return episode identities needed by AniSkip/TheIntroDB when cache is due."""
        watchlist_id = str(watchlist_local_id or "")
        if not watchlist_id:
            return []
        now = int(now_epoch or time.time())
        with self._connection() as db:
            watchlist_columns = {
                row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")
            }
            special_expression = (
                "watchlist_items.special_locator"
                if "special_locator" in watchlist_columns else "NULL"
            )
            query = """SELECT episodes.local_id AS episode_local_id,
              episodes.episode_number,episodes.source_episode_number,
              episodes.release_date,episodes.runtime_minutes,
              seasons.local_id AS season_local_id,seasons.season_number,
              tv_series.local_id AS series_local_id,tv_series.tvdb_id,
              COALESCE(episodes.mal_id,watchlist_items.mal_id,seasons.mal_id)
                AS timestamp_mal_id,
              {special_expression} AS special_locator,
              episode_timestamp_state.status AS timestamp_status,
              episode_timestamp_state.checked_epoch AS timestamp_checked_epoch
              FROM episodes
              JOIN seasons ON seasons.local_id=episodes.related_season_id
              JOIN tv_series ON tv_series.local_id=seasons.related_series_id
              LEFT JOIN watchlist_items
                ON watchlist_items.local_id=episodes.watchlist_local_id
              LEFT JOIN episode_timestamp_state
                ON episode_timestamp_state.episode_local_id=episodes.local_id
              WHERE episodes.watchlist_local_id=?""".format(
                special_expression=special_expression
            )
            params = [watchlist_id]
            if series_id not in (None, ""):
                query += " AND tv_series.local_id=?"
                params.append(str(series_id))
            query += " ORDER BY seasons.season_number,episodes.episode_number"
            rows = [dict(row) for row in db.execute(query, params)]

        result = []
        for row in rows:
            if not force and row.get("timestamp_status"):
                ttl = TIMESTAMP_TTL.get(str(row["timestamp_status"]), 0)
                checked = int(row.get("timestamp_checked_epoch") or 0)
                if checked and checked + ttl > now:
                    continue
            season_number, episode_number = self._timestamp_coordinate(
                row.get("season_number"), row.get("episode_number"),
                row.get("special_locator"),
            )
            row["timestamp_season_number"] = season_number
            row["timestamp_episode_number"] = episode_number
            result.append(row)
        return result

    def replace_episode_segments(self, episode_id, segments, status="FOUND", error=None,
                                 checked_epoch=None):
        episode_id = str(episode_id)
        status = str(status or "EMPTY").upper()
        if status not in ("FOUND", "EMPTY", "ERROR"):
            raise ValueError("invalid timestamp status")
        checked = int(checked_epoch or time.time())
        normalized = []
        counters = {}
        for segment in segments or []:
            segment_type = str(segment.get("type") or "").strip().lower()
            if segment_type not in TIMESTAMP_TYPES:
                continue
            try:
                start_ms = max(0, int(segment.get("start_ms") or 0))
                end_value = segment.get("end_ms")
                end_ms = None if end_value is None else max(0, int(end_value))
            except (TypeError, ValueError):
                continue
            if end_ms is not None and end_ms <= start_ms:
                continue
            index = counters.get(segment_type, 0)
            counters[segment_type] = index + 1
            source_duration = segment.get("source_duration_ms")
            try:
                source_duration = (
                    None if source_duration in (None, "")
                    else max(0, int(source_duration))
                )
            except (TypeError, ValueError):
                source_duration = None
            normalized.append((
                segment_type, index, start_ms, end_ms,
                str(segment.get("source") or "unknown"), source_duration,
                str(segment.get("source_ref") or "") or None,
            ))

        with self._connection() as db:
            if not db.execute(
                "SELECT 1 FROM episodes WHERE local_id=?", (episode_id,)
            ).fetchone():
                raise KeyError("episode not found")
            db.execute("DELETE FROM episode_segments WHERE episode_local_id=?", (episode_id,))
            for row in normalized:
                db.execute("""INSERT INTO episode_segments(
                  episode_local_id,segment_type,segment_index,start_ms,end_ms,
                  source,source_duration_ms,source_ref)
                  VALUES(?,?,?,?,?,?,?,?)""", (episode_id,) + row)
            db.execute("""INSERT INTO episode_timestamp_state(
              episode_local_id,status,checked_epoch,segment_count,last_error,updated_at)
              VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
              ON CONFLICT(episode_local_id) DO UPDATE SET
                status=excluded.status,checked_epoch=excluded.checked_epoch,
                segment_count=excluded.segment_count,last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP""",
                (episode_id, status, checked, len(normalized), error))
        return {
            "episode_id": episode_id,
            "status": status,
            "segment_count": len(normalized),
            "checked_epoch": checked,
        }

    def record_episode_timestamp_error(self, episode_id, error):
        metadata = self.episode_timestamp_metadata(episode_id)
        segments = metadata.get("segments") if metadata else []
        return self.replace_episode_segments(
            episode_id, segments, status="ERROR",
            error=str(error or "timestamp fetch failed")
        )

    def episode_segments(self, episode_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT segment_type AS type,
              start_ms,end_ms,source,source_duration_ms,source_ref,segment_index
              FROM episode_segments WHERE episode_local_id=?
              ORDER BY start_ms,segment_type,segment_index""", (str(episode_id),))]

    def episode_timestamp_metadata(self, episode_id):
        episode_id = str(episode_id)
        with self._connection() as db:
            episode = db.execute(
                "SELECT local_id FROM episodes WHERE local_id=?", (episode_id,)
            ).fetchone()
            if not episode:
                return None
            state = db.execute(
                "SELECT * FROM episode_timestamp_state WHERE episode_local_id=?",
                (episode_id,),
            ).fetchone()
            state = dict(state) if state else None
        return {
            "episode_id": episode_id,
            "status": state.get("status") if state else "UNFETCHED",
            "checked_epoch": int(state.get("checked_epoch") or 0) if state else 0,
            "last_error": state.get("last_error") if state else None,
            "segments": self.episode_segments(episode_id),
        }

    def library_series_detail(self, series_id):
        """Expose timestamp metadata alongside each episode in the detail model."""
        detail = super().library_series_detail(series_id)
        if not detail:
            return detail
        for season in detail.get("seasons") or []:
            for episode in season.get("episodes") or []:
                metadata = self.episode_timestamp_metadata(episode.get("local_id"))
                episode["timestamp_status"] = (
                    metadata.get("status") if metadata else "UNFETCHED"
                )
                episode["segments"] = metadata.get("segments") if metadata else []
        return detail
