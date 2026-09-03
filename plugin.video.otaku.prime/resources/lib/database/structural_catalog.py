# -*- coding: utf-8 -*-
"""Strict structural catalogue policy for mediated Prime library content."""
from __future__ import annotations

from resources.lib.database.runtime_catalog import RuntimeCatalogStore
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)


class StructuralCatalogConflict(RuntimeError):
    """A mediated placement would corrupt an existing structural identity."""


def _clean_id(value):
    text = str(value or "").strip()
    return text or None


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class StructuralCatalogStore(RuntimeCatalogStore):
    """Runtime catalogue that treats Kodi/TVDB coordinates as immutable structure.

    Provider relationships are many-to-one evidence.  They must never mutate an
    existing series owner, shared season identity, or already occupied structural
    episode coordinate.
    """

    # RuntimeCatalogStore used to compact S00 by release date.  That destroys
    # real TVDB coordinates (for example Monogatari S00E02/S00E19/S00E33), so
    # structural Prime deliberately disables that policy everywhere, including
    # the startup migration path and the post-add_episode hook.
    def _resequence_specials(self, season_id):
        return 0

    @staticmethod
    def _series_by(db, column, value):
        value = _clean_id(value)
        if not value:
            return None
        row = db.execute(
            "SELECT * FROM tv_series WHERE {}=?".format(column), (value,)
        ).fetchone()
        return dict(row) if row else None

    def get_or_create_series(self, english_name=None, romaji_name=None,
                             root_simkl_id=None, tvdb_id=None,
                             root_anilist_id=None, source_provider=None,
                             source_media_format=None, publish_year=None,
                             overview=None, runtime_minutes=None, air_status=None,
                             poster_url=None, fanart_url=None, clearlogo_url=None,
                             banner_url=None, genres=None, themes=None,
                             age_rating=None, mature=False):
        """Resolve by structural TVDB identity without stealing provider IDs.

        TVDB is the strongest persisted series-owner key available to Prime.  If
        an incoming AniList/Simkl identity already belongs to a different TVDB
        series, the weak identity is ignored for this merge rather than reassigned.
        """
        root_simkl_id = _clean_id(root_simkl_id)
        root_anilist_id = _clean_id(root_anilist_id)
        tvdb_id = _clean_id(tvdb_id)

        with self._connection() as db:
            by_tvdb = self._series_by(db, "tvdb_id", tvdb_id)
            by_simkl = self._series_by(db, "root_simkl_id", root_simkl_id)
            by_anilist = self._series_by(db, "root_anilist_id", root_anilist_id)

        if tvdb_id:
            for label, owner in (("root_simkl_id", by_simkl),
                                 ("root_anilist_id", by_anilist)):
                if not owner:
                    continue
                owner_tvdb = _clean_id(owner.get("tvdb_id"))
                if owner_tvdb and owner_tvdb != tvdb_id:
                    LOGGER.warning(
                        "Rejected cross-series identity during structural merge: "
                        "incoming tvdb=%s %s belongs to Prime series %s tvdb=%s",
                        tvdb_id, label, owner.get("local_id"), owner_tvdb,
                    )
                    if label == "root_simkl_id":
                        root_simkl_id = None
                    else:
                        root_anilist_id = None

            if by_tvdb:
                # Once a TVDB series exists, a later related season/special may
                # link to it but may not replace the canonical provider roots.
                existing_simkl = _clean_id(by_tvdb.get("root_simkl_id"))
                existing_anilist = _clean_id(by_tvdb.get("root_anilist_id"))
                if existing_simkl and root_simkl_id and existing_simkl != root_simkl_id:
                    LOGGER.info(
                        "Preserved canonical Simkl series identity for TVDB %s: %s; "
                        "ignored related identity %s",
                        tvdb_id, existing_simkl, root_simkl_id,
                    )
                    root_simkl_id = None
                if existing_anilist and root_anilist_id and existing_anilist != root_anilist_id:
                    LOGGER.info(
                        "Preserved canonical AniList series identity for TVDB %s: %s; "
                        "ignored related identity %s",
                        tvdb_id, existing_anilist, root_anilist_id,
                    )
                    root_anilist_id = None
                # Canonical structural naming must not drift to the most recently
                # mediated season/special.
                english_name = by_tvdb.get("english_name") or english_name
                romaji_name = by_tvdb.get("romaji_name") or romaji_name
                source_media_format = (
                    by_tvdb.get("source_media_format") or source_media_format
                )
                publish_year = by_tvdb.get("publish_year") or publish_year
        else:
            owners = [row for row in (by_simkl, by_anilist) if row]
            owner_ids = {row["local_id"] for row in owners}
            if len(owner_ids) > 1:
                raise StructuralCatalogConflict(
                    "provider series identities resolve to different Prime series "
                    "without a structural TVDB owner"
                )

        return super().get_or_create_series(
            english_name=english_name,
            romaji_name=romaji_name,
            root_simkl_id=root_simkl_id,
            tvdb_id=tvdb_id,
            root_anilist_id=root_anilist_id,
            source_provider=source_provider,
            source_media_format=source_media_format,
            publish_year=publish_year,
            overview=overview,
            runtime_minutes=runtime_minutes,
            air_status=air_status,
            poster_url=poster_url,
            fanart_url=fanart_url,
            clearlogo_url=clearlogo_url,
            banner_url=banner_url,
            genres=genres,
            themes=themes,
            age_rating=age_rating,
            mature=mature,
        )

    def add_watchlist_season(self, series_id, watchlist_item, season_number=None,
                             provider_path=None, placement_source=None,
                             first_episode=None, last_episode=None,
                             english_name=None, romaji_name=None,
                             release_date=None, release_status=None,
                             placement_state="COMPLETE"):
        """Link another source item without mutating a shared structural season."""
        series_id = str(series_id)
        watchlist_id = str(watchlist_item["local_id"])
        number = _int_or_none(season_number)
        if season_number is not None and number is None:
            number = 0 if int(season_number) == 0 else None

        if number is not None:
            with self._connection() as db:
                row = db.execute(
                    "SELECT * FROM seasons WHERE related_series_id=? AND season_number=?",
                    (series_id, number),
                ).fetchone()
                if row:
                    row = dict(row)
                    db.execute(
                        "INSERT OR IGNORE INTO season_watchlist_links("
                        "season_local_id,watchlist_local_id) VALUES(?,?)",
                        (row["local_id"], watchlist_id),
                    )
                    current_first = _int_or_none(row.get("first_episode"))
                    current_last = _int_or_none(row.get("last_episode"))
                    incoming_first = _int_or_none(first_episode)
                    incoming_last = _int_or_none(last_episode)
                    values_first = [value for value in (current_first, incoming_first)
                                    if value is not None]
                    values_last = [value for value in (current_last, incoming_last)
                                   if value is not None]
                    merged_first = min(values_first) if values_first else None
                    merged_last = max(values_last) if values_last else None
                    state = (
                        "COMPLETE"
                        if placement_state == "COMPLETE" or row.get("placement_state") == "COMPLETE"
                        else "STRUCTURE_ONLY"
                    )
                    db.execute(
                        "UPDATE seasons SET first_episode=?,last_episode=?,"
                        "placement_state=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
                        (merged_first, merged_last, state, row["local_id"]),
                    )
                    refreshed = db.execute(
                        "SELECT * FROM seasons WHERE local_id=?", (row["local_id"],)
                    ).fetchone()
                    LOGGER.info(
                        "Linked watchlist item %s to immutable shared Prime season %s S%02d",
                        watchlist_id, row["local_id"], number,
                    )
                    return dict(refreshed)

        return super().add_watchlist_season(
            series_id,
            watchlist_item,
            season_number=season_number,
            provider_path=provider_path,
            placement_source=placement_source,
            first_episode=first_episode,
            last_episode=last_episode,
            english_name=english_name,
            romaji_name=romaji_name,
            release_date=release_date,
            release_status=release_status,
            placement_state=placement_state,
        )

    def _runtime_episode_number(self, season_id, episode_number,
                                source_episode_number=None,
                                watchlist_local_id=None):
        # Structural coordinates are never compacted or appended.  Collision
        # handling happens in add_episode where provider identities are visible.
        return int(episode_number)

    @staticmethod
    def _same_episode_identity(existing, incoming):
        for key in ("simkl_id", "anilist_id", "mal_id", "kitsu_id"):
            left = _clean_id(existing.get(key))
            right = _clean_id(incoming.get(key))
            if left and right and left == right:
                return True
        return False

    def add_episode(self, season_id, episode_number, source_episode_number=None,
                    mal_id=None, simkl_id=None, anilist_id=None, kitsu_id=None,
                    watch_status=None, release_date=None, title=None,
                    overview=None, runtime_minutes=None,
                    watchlist_local_id=None):
        """Preserve an exact coordinate or reject an incompatible collision."""
        season_id = str(season_id)
        number = int(episode_number)
        source_number = int(source_episode_number or number)
        incoming = {
            "mal_id": mal_id,
            "simkl_id": simkl_id,
            "anilist_id": anilist_id,
            "kitsu_id": kitsu_id,
        }
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM episodes WHERE related_season_id=? AND episode_number=?",
                (season_id, number),
            ).fetchone()
            existing = dict(row) if row else None

        if existing:
            existing_watchlist = _clean_id(existing.get("watchlist_local_id"))
            incoming_watchlist = _clean_id(watchlist_local_id)
            same_source = (
                existing_watchlist == incoming_watchlist
                and int(existing.get("source_episode_number") or number) == source_number
            )
            if not same_source:
                if self._same_episode_identity(existing, incoming):
                    LOGGER.info(
                        "Reused structural Prime episode %s for related watchlist item %s "
                        "at exact coordinate E%02d",
                        existing["local_id"], incoming_watchlist, number,
                    )
                    return existing
                raise StructuralCatalogConflict(
                    "structural episode coordinate collision at {} E{}: existing "
                    "watchlist {} source {} conflicts with incoming watchlist {} source {}"
                    .format(
                        season_id, number, existing_watchlist,
                        existing.get("source_episode_number"), incoming_watchlist,
                        source_number,
                    )
                )

        return super().add_episode(
            season_id,
            number,
            source_episode_number=source_number,
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
