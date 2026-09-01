# -*- coding: utf-8 -*-
"""Runtime catalogue write policy for shared Prime seasons."""
from __future__ import annotations

from resources.lib.database.catalog import CatalogStore
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)


class RuntimeCatalogStore(CatalogStore):
    """CatalogStore with stable multipart episode-coordinate allocation.

    Prime intentionally shares one catalogue season between watchlist items when
    several tracker entries belong to the same Kodi season. Provider coordinates
    are preferred when they are free. If another watchlist item already owns the
    requested coordinate, the incoming part is appended to the next free episode
    number instead of failing mediation.

    Existing ``watchlist_local_id + source_episode_number`` mappings always win.
    That keeps a previously allocated Prime episode stable across metadata
    refreshes and also prevents a later source episode from overwriting an earlier
    source episode that had already been moved because of a collision.
    """

    def initialize(self):
        super().initialize()
        with self._connection() as db:
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
