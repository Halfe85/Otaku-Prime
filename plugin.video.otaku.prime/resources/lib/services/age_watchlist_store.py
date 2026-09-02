# -*- coding: utf-8 -*-
"""Watchdog storage policy where age restrictions affect Kodi, not Prime metadata."""
from __future__ import annotations

from resources.lib.services.watchlist_watchdog import WatchlistWatchdogStore


class AgePolicyWatchlistWatchdogStore(WatchlistWatchdogStore):
    """Always mediate connected watchlist rows, including Rx/adult entries.

    AgeContentPolicyStore is the sole authority deciding whether completed
    catalogue rows may be projected into Kodi. Prime's own UI therefore retains
    metadata for restricted entries instead of making them disappear.
    """

    def list_ui_items(self):
        with self._connection() as db:
            result = [dict(row) for row in db.execute("""SELECT
              item.local_id,item.anilist_id,item.mal_id,item.kitsu_id,item.simkl_id,
              item.simkl_reference_id,item.special_locator,
              item.english_name,item.preferred_name,item.romaji_name,item.native_name,
              item.alternative_titles_json,
              item.status,item.progress,item.episode_count,item.media_format,item.release_date,
              item.is_adult,
              item.has_conflict,item.identity_resolution_status,item.identity_resolution_error,
              item.mediator_status,item.mediator_provider,item.mediator_error,
              item.mediator_ready,item.added_to_library,
              GROUP_CONCAT(entry.provider,',') AS connected_providers
              FROM watchlist_items item
              JOIN watchlist_provider_entries entry ON entry.local_id=item.local_id
              GROUP BY item.local_id ORDER BY
              CASE WHEN COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'')),item.local_id""")]
            for item in result:
                item["alternative_titles"] = self._alternative_titles(
                    item.pop("alternative_titles_json", None)
                )
            return result

    def list_watchdog_work(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE (added_to_library=0 OR (
                (anilist_id IS NULL OR mal_id IS NULL OR kitsu_id IS NULL OR
                 (simkl_id IS NULL AND simkl_reference_id IS NULL))
                AND COALESCE(identity_resolution_status,'PENDING') NOT IN('CONFLICT_EXACT')))
              ORDER BY CASE WHEN COALESCE(english_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(english_name,romaji_name,native_name,'')),local_id""")]

    def list_mediator_ready(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE mediator_ready=1 AND added_to_library=0
              ORDER BY CASE WHEN COALESCE(english_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(english_name,romaji_name,native_name,'')),local_id""")]
