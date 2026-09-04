# -*- coding: utf-8 -*-
"""Immutable Prime franchise ownership with separate structural TVDB evidence."""
from __future__ import annotations

from resources.lib.database.runtime_catalog import RuntimeCatalogStore
from resources.lib.database.structural_catalog import (
    StructuralCatalogConflict,
    StructuralCatalogStore,
)
from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
FRANCHISE_MEDIATOR_REVISION = "franchise-owner-physical-identity-2"


def _clean(value):
    text = str(value or "").strip()
    return text or None


class FranchiseCatalogStore(StructuralCatalogStore):
    """Keep the parent franchise immutable after its first exact-ID creation.

    TVDB mappings describe structural source coordinates.  They do not own the
    Prime franchise row and are persisted per watchlist/season mapping instead.
    """

    def initialize(self):
        super().initialize()
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS season_structural_sources(
              season_local_id TEXT NOT NULL,
              watchlist_local_id TEXT NOT NULL,
              structural_tvdb_id TEXT,
              structural_simkl_id TEXT,
              structural_name TEXT,
              structural_season_number INTEGER,
              source_provider TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(season_local_id,watchlist_local_id),
              FOREIGN KEY(season_local_id) REFERENCES seasons(local_id) ON DELETE CASCADE,
              FOREIGN KEY(watchlist_local_id) REFERENCES watchlist_items(local_id) ON DELETE CASCADE
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS ix_season_structural_tvdb
              ON season_structural_sources(structural_tvdb_id,structural_season_number)""")

    def structural_rebuild_required(self):
        with self._connection() as db:
            row = db.execute(
                "SELECT value FROM prime_catalog_state WHERE key='franchise_mediator_revision'"
            ).fetchone()
        return not row or str(row["value"] or "") != FRANCHISE_MEDIATOR_REVISION

    def reset_structural_projection(self):
        """Clear generated catalogue and requeue every source marked as projected."""
        with self._connection() as db:
            current = db.execute(
                "SELECT value FROM prime_catalog_state WHERE key='franchise_mediator_revision'"
            ).fetchone()
            if current and str(current["value"] or "") == FRANCHISE_MEDIATOR_REVISION:
                return {"rebuilt": False, "watchlist_items": 0, "series": 0, "movies": 0}

            series_count = int(db.execute("SELECT COUNT(*) FROM tv_series").fetchone()[0])
            movie_count = int(db.execute("SELECT COUNT(*) FROM movies").fetchone()[0])
            linked = []
            watchlist_exists = bool(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_items'"
            ).fetchone())
            if watchlist_exists:
                linked = [str(row[0]) for row in db.execute("""
                  SELECT local_id FROM watchlist_items WHERE added_to_library=1
                  UNION
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
                      mediator_error='Franchise ownership/physical identity rebuild required',
                      mediator_checked_at=NULL,updated_at=CURRENT_TIMESTAMP
                      WHERE local_id IN ({})""".format(placeholders), linked)

            db.execute("DELETE FROM movies")
            db.execute("DELETE FROM tv_series")
            db.execute("""INSERT INTO prime_catalog_state(key,value)
              VALUES('franchise_mediator_revision',?)
              ON CONFLICT(key) DO UPDATE SET value=excluded.value,
              updated_at=CURRENT_TIMESTAMP""", (FRANCHISE_MEDIATOR_REVISION,))

        LOGGER.warning(
            "Reset Prime catalogue for immutable franchise revision %s: "
            "series=%s movies=%s watchlist_items=%s",
            FRANCHISE_MEDIATOR_REVISION, series_count, movie_count, len(linked),
        )
        return {
            "rebuilt": True,
            "watchlist_items": len(linked),
            "series": series_count,
            "movies": movie_count,
        }

    @staticmethod
    def _series_by(db, column, value):
        value = _clean(value)
        if not value:
            return None
        row = db.execute(
            "SELECT * FROM tv_series WHERE {}=?".format(column), (value,)
        ).fetchone()
        return dict(row) if row else None

    def _base_store_series(self, **kwargs):
        # Bypass StructuralCatalogStore's old TVDB-as-series-owner policy while
        # retaining RuntimeCatalogStore storage/migrations underneath it.
        return RuntimeCatalogStore.get_or_create_series(self, **kwargs)

    def get_or_create_series(self, english_name=None, romaji_name=None,
                             root_simkl_id=None, tvdb_id=None,
                             root_anilist_id=None, source_provider=None,
                             source_media_format=None, publish_year=None,
                             overview=None, runtime_minutes=None, air_status=None,
                             poster_url=None, fanart_url=None, clearlogo_url=None,
                             banner_url=None, genres=None, themes=None,
                             age_rating=None, mature=False):
        """Resolve only by exact franchise IDs; never fuzzy-merge a new special.

        Once an existing parent is selected, title/year/root IDs are copied from
        that row before storage so a later season, PV or special cannot rename or
        re-root the franchise.
        """
        simkl = _clean(root_simkl_id)
        anilist = _clean(root_anilist_id)
        tvdb = _clean(tvdb_id)
        with self._connection() as db:
            by_simkl = self._series_by(db, "root_simkl_id", simkl)
            by_anilist = self._series_by(db, "root_anilist_id", anilist)
            by_tvdb = self._series_by(db, "tvdb_id", tvdb)

        # Provider franchise roots are authoritative.  TVDB is a fallback only
        # when no provider root exists; target structural TVDB IDs live elsewhere.
        candidates = [row for row in (by_simkl, by_anilist) if row]
        if not candidates and by_tvdb:
            candidates = [by_tvdb]
        owner_ids = {row["local_id"] for row in candidates}
        if len(owner_ids) > 1:
            raise StructuralCatalogConflict(
                "exact franchise identities resolve to different Prime series: {}".format(
                    ", ".join(sorted(owner_ids))
                )
            )

        owner = candidates[0] if candidates else None
        if owner:
            # These fields define parent identity and are immutable after creation.
            english_name = owner.get("english_name") or english_name
            romaji_name = owner.get("romaji_name") or romaji_name
            simkl = _clean(owner.get("root_simkl_id")) or simkl
            anilist = _clean(owner.get("root_anilist_id")) or anilist
            tvdb = _clean(owner.get("tvdb_id")) or tvdb
            publish_year = owner.get("publish_year") or publish_year
            source_media_format = owner.get("source_media_format") or source_media_format
            return self._base_store_series(
                english_name=english_name, romaji_name=romaji_name,
                root_simkl_id=simkl, tvdb_id=tvdb,
                root_anilist_id=anilist, source_provider=source_provider,
                source_media_format=source_media_format, publish_year=publish_year,
                overview=overview, runtime_minutes=runtime_minutes,
                air_status=air_status, poster_url=poster_url,
                fanart_url=fanart_url, clearlogo_url=clearlogo_url,
                banner_url=banner_url, genres=genres, themes=themes,
                age_rating=age_rating, mature=mature,
            )

        # The base catalogue has historical fuzzy-title repair.  New franchise
        # owners must never use it.  Create the exact remote identity without a
        # title first, then apply display metadata by the newly created exact ID.
        created = self._base_store_series(
            english_name=None, romaji_name=None,
            root_simkl_id=simkl, tvdb_id=tvdb,
            root_anilist_id=anilist, source_provider=source_provider,
            source_media_format=source_media_format, publish_year=publish_year,
            overview=overview, runtime_minutes=runtime_minutes,
            air_status=air_status, poster_url=poster_url,
            fanart_url=fanart_url, clearlogo_url=clearlogo_url,
            banner_url=banner_url, genres=genres, themes=themes,
            age_rating=age_rating, mature=mature,
        )
        LOGGER.info(
            "Created Prime franchise %s by exact identity before title metadata",
            created.get("local_id"),
        )
        return self._base_store_series(
            english_name=english_name, romaji_name=romaji_name,
            root_simkl_id=simkl, tvdb_id=tvdb,
            root_anilist_id=anilist, source_provider=source_provider,
            source_media_format=source_media_format, publish_year=publish_year,
            overview=overview, runtime_minutes=runtime_minutes,
            air_status=air_status, poster_url=poster_url,
            fanart_url=fanart_url, clearlogo_url=clearlogo_url,
            banner_url=banner_url, genres=genres, themes=themes,
            age_rating=age_rating, mature=mature,
        )

    def set_watchlist_structural_owner(self, season_id, watchlist_local_id,
                                       owner, structural_season_number=None,
                                       source_provider=None):
        """Persist structural TVDB evidence without mutating franchise identity."""
        owner = dict(owner or {})
        tvdb = _clean(owner.get("tvdb_id"))
        simkl = _clean(owner.get("simkl_id"))
        name = _clean(owner.get("name"))
        try:
            number = (
                int(structural_season_number)
                if structural_season_number is not None else None
            )
        except (TypeError, ValueError):
            number = None
        with self._connection() as db:
            if not db.execute(
                "SELECT 1 FROM seasons WHERE local_id=?", (str(season_id),)
            ).fetchone():
                raise KeyError("Prime season not found")
            db.execute("""INSERT INTO season_structural_sources(
              season_local_id,watchlist_local_id,structural_tvdb_id,
              structural_simkl_id,structural_name,structural_season_number,
              source_provider)
              VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(season_local_id,watchlist_local_id) DO UPDATE SET
                structural_tvdb_id=COALESCE(excluded.structural_tvdb_id,structural_tvdb_id),
                structural_simkl_id=COALESCE(excluded.structural_simkl_id,structural_simkl_id),
                structural_name=COALESCE(excluded.structural_name,structural_name),
                structural_season_number=COALESCE(
                  excluded.structural_season_number,structural_season_number),
                source_provider=COALESCE(excluded.source_provider,source_provider),
                updated_at=CURRENT_TIMESTAMP""",
                (str(season_id), str(watchlist_local_id), tvdb, simkl, name,
                 number, _clean(source_provider)))
        return {
            "season_id": str(season_id),
            "watchlist_local_id": str(watchlist_local_id),
            "structural_tvdb_id": tvdb,
            "structural_simkl_id": simkl,
            "structural_name": name,
            "structural_season_number": number,
        }
