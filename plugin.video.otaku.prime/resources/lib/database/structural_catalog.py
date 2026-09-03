# -*- coding: utf-8 -*-
"""Strict structural catalogue policy for mediated Prime library content."""
from __future__ import annotations

from resources.lib.database.runtime_catalog import RuntimeCatalogStore
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
STRUCTURAL_MEDIATOR_REVISION = "structural-owner-coordinates-1"


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
    """Runtime catalogue that treats Kodi/TVDB coordinates as immutable structure."""

    def _resequence_specials(self, season_id):
        """Never compact sparse S00 TVDB coordinates by release order."""
        return 0

    def structural_rebuild_required(self):
        """Return whether generated catalogue rows predate structural mediation."""
        with self._connection() as db:
            row = db.execute(
                "SELECT value FROM prime_catalog_state WHERE key='structural_mediator_revision'"
            ).fetchone()
        return not row or str(row["value"] or "") != STRUCTURAL_MEDIATOR_REVISION

    def reset_structural_projection(self):
        """Drop generated catalogue only and requeue its watchlist sources once.

        Watchlist/provider/account/profile state is preserved. The physical layer
        removes generated Kodi files before calling this method so the same source
        items can be mediated again through the new structural rules.
        """
        with self._connection() as db:
            current = db.execute(
                "SELECT value FROM prime_catalog_state WHERE key='structural_mediator_revision'"
            ).fetchone()
            if current and str(current["value"] or "") == STRUCTURAL_MEDIATOR_REVISION:
                return {"rebuilt": False, "watchlist_items": 0, "series": 0, "movies": 0}

            series_count = int(db.execute("SELECT COUNT(*) FROM tv_series").fetchone()[0])
            movie_count = int(db.execute("SELECT COUNT(*) FROM movies").fetchone()[0])
            watchlist_exists = bool(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_items'"
            ).fetchone())
            linked = []
            if watchlist_exists:
                linked = [str(row[0]) for row in db.execute("""
                  SELECT watchlist_local_id FROM season_watchlist_links
                    WHERE watchlist_local_id IS NOT NULL
                  UNION
                  SELECT watchlist_local_id FROM seasons
                    WHERE watchlist_local_id IS NOT NULL
                  UNION
                  SELECT watchlist_local_id FROM movies
                    WHERE watchlist_local_id IS NOT NULL
                """).fetchall()]
                if linked:
                    placeholders = ",".join("?" for _ in linked)
                    db.execute("""UPDATE watchlist_items SET
                      added_to_library=0,library_added_at=NULL,mediator_ready=1,
                      mediator_status='PARTIAL',mediator_provider=NULL,
                      mediator_error='Structural mediator rebuild required',
                      mediator_checked_at=NULL,updated_at=CURRENT_TIMESTAMP
                      WHERE local_id IN ({})""".format(placeholders), linked)

            # Generated catalogue only. Cascades remove seasons, episodes and
            # media-credit links while watchlist/provider source state survives.
            db.execute("DELETE FROM movies")
            db.execute("DELETE FROM tv_series")
            db.execute("""INSERT INTO prime_catalog_state(key,value)
              VALUES('structural_mediator_revision',?)
              ON CONFLICT(key) DO UPDATE SET value=excluded.value,
              updated_at=CURRENT_TIMESTAMP""", (STRUCTURAL_MEDIATOR_REVISION,))

        LOGGER.warning(
            "Reset Prime structural catalogue for mediator revision %s: "
            "series=%s movies=%s watchlist_items=%s",
            STRUCTURAL_MEDIATOR_REVISION, series_count, movie_count, len(linked),
        )
        return {
            "rebuilt": True,
            "watchlist_items": len(linked),
            "series": series_count,
            "movies": movie_count,
        }

    @staticmethod
    def _series_by(db, column, value):
        value = _clean_id(value)
        if not value:
            return None
        row = db.execute(
            "SELECT * FROM tv_series WHERE {}=?".format(column), (value,)
        ).fetchone()
        return dict(row) if row else None

    def _store_series(self, **kwargs):
        return super().get_or_create_series(**kwargs)

    def get_or_create_series(self, english_name=None, romaji_name=None,
                             root_simkl_id=None, tvdb_id=None,
                             root_anilist_id=None, source_provider=None,
                             source_media_format=None, publish_year=None,
                             overview=None, runtime_minutes=None, air_status=None,
                             poster_url=None, fanart_url=None, clearlogo_url=None,
                             banner_url=None, genres=None, themes=None,
                             age_rating=None, mature=False):
        """Resolve by structural TVDB identity without stealing provider IDs."""
        root_simkl_id = _clean_id(root_simkl_id)
        root_anilist_id = _clean_id(root_anilist_id)
        tvdb_id = _clean_id(tvdb_id)
        requested_english = english_name
        requested_romaji = romaji_name

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
                existing_simkl = _clean_id(by_tvdb.get("root_simkl_id"))
                existing_anilist = _clean_id(by_tvdb.get("root_anilist_id"))
                if existing_simkl and root_simkl_id and existing_simkl != root_simkl_id:
                    root_simkl_id = None
                if existing_anilist and root_anilist_id and existing_anilist != root_anilist_id:
                    root_anilist_id = None
                english_name = by_tvdb.get("english_name") or english_name
                romaji_name = by_tvdb.get("romaji_name") or romaji_name
                source_media_format = by_tvdb.get("source_media_format") or source_media_format
                publish_year = by_tvdb.get("publish_year") or publish_year
            else:
                validated_provider_owners = []
                if root_simkl_id and by_simkl:
                    validated_provider_owners.append(by_simkl)
                if root_anilist_id and by_anilist:
                    validated_provider_owners.append(by_anilist)
                validated_ids = {
                    row["local_id"] for row in validated_provider_owners
                    if not _clean_id(row.get("tvdb_id"))
                    or _clean_id(row.get("tvdb_id")) == tvdb_id
                }
                if len(validated_ids) > 1:
                    raise StructuralCatalogConflict(
                        "provider identities disagree while assigning TVDB {}".format(tvdb_id)
                    )
                if not validated_ids:
                    created = self._store_series(
                        english_name=None, romaji_name=None,
                        root_simkl_id=root_simkl_id, tvdb_id=tvdb_id,
                        root_anilist_id=root_anilist_id,
                        source_provider=source_provider,
                        source_media_format=source_media_format,
                        publish_year=publish_year, overview=overview,
                        runtime_minutes=runtime_minutes, air_status=air_status,
                        poster_url=poster_url, fanart_url=fanart_url,
                        clearlogo_url=clearlogo_url, banner_url=banner_url,
                        genres=genres, themes=themes, age_rating=age_rating,
                        mature=mature,
                    )
                    LOGGER.info(
                        "Created structurally distinct Prime series %s for TVDB %s "
                        "before applying fuzzy-matchable title metadata",
                        created.get("local_id"), tvdb_id,
                    )
                    return self._store_series(
                        english_name=requested_english, romaji_name=requested_romaji,
                        root_simkl_id=root_simkl_id, tvdb_id=tvdb_id,
                        root_anilist_id=root_anilist_id,
                        source_provider=source_provider,
                        source_media_format=source_media_format,
                        publish_year=publish_year, overview=overview,
                        runtime_minutes=runtime_minutes, air_status=air_status,
                        poster_url=poster_url, fanart_url=fanart_url,
                        clearlogo_url=clearlogo_url, banner_url=banner_url,
                        genres=genres, themes=themes, age_rating=age_rating,
                        mature=mature,
                    )
        else:
            owners = [row for row in (by_simkl, by_anilist) if row]
            owner_ids = {row["local_id"] for row in owners}
            if len(owner_ids) > 1:
                raise StructuralCatalogConflict(
                    "provider series identities resolve to different Prime series "
                    "without a structural TVDB owner"
                )

        return self._store_series(
            english_name=english_name, romaji_name=romaji_name,
            root_simkl_id=root_simkl_id, tvdb_id=tvdb_id,
            root_anilist_id=root_anilist_id, source_provider=source_provider,
            source_media_format=source_media_format, publish_year=publish_year,
            overview=overview, runtime_minutes=runtime_minutes,
            air_status=air_status, poster_url=poster_url, fanart_url=fanart_url,
            clearlogo_url=clearlogo_url, banner_url=banner_url,
            genres=genres, themes=themes, age_rating=age_rating, mature=mature,
        )

    def add_watchlist_season(self, series_id, watchlist_item, season_number=None,
                             provider_path=None, placement_source=None,
                             first_episode=None, last_episode=None,
                             english_name=None, romaji_name=None,
                             release_date=None, release_status=None,
                             placement_state="COMPLETE"):
        """Link source media to one immutable structural season."""
        series_id = str(series_id)
        watchlist_id = str(watchlist_item["local_id"])
        number = _int_or_none(season_number)

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
                    values_first = [v for v in (current_first, incoming_first) if v is not None]
                    values_last = [v for v in (current_last, incoming_last) if v is not None]
                    state = (
                        "COMPLETE" if placement_state == "COMPLETE"
                        or row.get("placement_state") == "COMPLETE"
                        else "STRUCTURE_ONLY"
                    )
                    db.execute(
                        "UPDATE seasons SET first_episode=?,last_episode=?,"
                        "placement_state=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
                        (min(values_first) if values_first else None,
                         max(values_last) if values_last else None,
                         state, row["local_id"]),
                    )
                    return dict(db.execute(
                        "SELECT * FROM seasons WHERE local_id=?", (row["local_id"],)
                    ).fetchone())

        source_item = watchlist_item
        if number == 0:
            # S00 is a structural container shared by many unrelated provider
            # media entries. Provider IDs live on watchlist links/episodes, not
            # on the Specials season row itself.
            source_item = dict(watchlist_item)
            for provider in ("anilist", "mal", "kitsu", "simkl"):
                source_item[provider + "_id"] = None
            source_item["media_format"] = "SPECIAL"
            english_name = "Specials"
            romaji_name = None

        return super().add_watchlist_season(
            series_id, source_item, season_number=season_number,
            provider_path=provider_path, placement_source=placement_source,
            first_episode=first_episode, last_episode=last_episode,
            english_name=english_name, romaji_name=romaji_name,
            release_date=release_date, release_status=release_status,
            placement_state=placement_state,
        )

    def _runtime_episode_number(self, season_id, episode_number,
                                source_episode_number=None,
                                watchlist_local_id=None):
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
            "mal_id": mal_id, "simkl_id": simkl_id,
            "anilist_id": anilist_id, "kitsu_id": kitsu_id,
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
            season_id, number, source_episode_number=source_number,
            mal_id=mal_id, simkl_id=simkl_id, anilist_id=anilist_id,
            kitsu_id=kitsu_id, watch_status=watch_status,
            release_date=release_date, title=title, overview=overview,
            runtime_minutes=runtime_minutes,
            watchlist_local_id=watchlist_local_id,
        )
