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
            LOGGER.warning(
                "Prime season coordinate collision: S%02dE%02d is owned by watchlist "
                "item %s; mapped watchlist item %s source episode %s to E%02d",
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
        return super().add_episode(
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
