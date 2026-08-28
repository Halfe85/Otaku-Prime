from __future__ import annotations

import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.watchlist_release import WatchlistReleaseManager, release_epoch


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.sqlite")
        self.watchlist = WatchlistItemStore(self.path)
        self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist", [{
            "provider_item_id": "100",
            "ids": {"anilist": "100", "mal": "200", "kitsu": "300", "simkl": "400"},
            "english_name": "Example Season",
            "romaji_name": "Example Season",
            "list_status": "CURRENT",
            "provider_status": "CURRENT",
            "progress": 1,
            "episode_count": 2,
            "media_format": "TV",
            "release_date": "2030-01-01",
            "provider_updated_at": "2030-01-02T00:00:00Z",
            "raw": {},
        }])
        self.watchlist.finalize_merge()
        self.item = self.watchlist.list_all()[0]

        self.catalog = CatalogStore(self.path, SegmentFactory())
        self.catalog.initialize()
        self.release = WatchlistReleaseManager(self.watchlist)
        self.release.initialize()

        self.series = self.catalog.get_or_create_series(
            english_name="Example Series",
            romaji_name="Example Series Romaji",
            root_simkl_id="399",
            root_anilist_id="99",
            tvdb_id="999",
            source_provider="simkl",
            source_media_format="TV",
            publish_year=2030,
            overview="A mediated series overview.",
            runtime_minutes=24,
            air_status="airing",
        )
        self.catalog.replace_series_cast(self.series["local_id"], [
            {"person_name": "Actor One", "character_name": "Hero", "sort_order": 0},
            {"person_name": "Actor Two", "character_name": "Rival", "sort_order": 1},
        ], source_provider="simkl")
        self.season = self.catalog.add_watchlist_season(
            self.series["local_id"], self.item, season_number=1,
            provider_path="simkl", placement_source="mapped_tvdb_seasons",
            first_episode=1, last_episode=2,
        )
        self.ep1 = self.catalog.add_episode(
            self.season["local_id"], 1, source_episode_number=1,
            simkl_id="401", title="Arrival", overview="Episode one overview.",
            runtime_minutes=24, release_date="2030-01-01",
        )
        self.ep2 = self.catalog.add_episode(
            self.season["local_id"], 2, source_episode_number=2,
            simkl_id="402", title="Return", overview="Episode two overview.",
            runtime_minutes=25, release_date="2030-01-08",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_tile_aggregates_series_counts_and_next_release(self):
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-05T12:00:00Z"), force=True
        )
        rows = self.catalog.library_series()
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(self.series["local_id"], row["local_id"])
        self.assertEqual("Example Series", row["title"])
        self.assertEqual(2030, row["publish_year"])
        self.assertEqual(1, row["season_count"])
        self.assertEqual(2, row["episode_count"])
        self.assertEqual(2, row["next_episode_number"])
        self.assertEqual("2030-01-08", row["next_episode_release_date"])
        self.assertEqual("RUNNING", row["library_status"])

    def test_series_detail_contains_cast_seasons_and_episode_metadata(self):
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual("A mediated series overview.", detail["overview"])
        self.assertEqual(24, detail["runtime_minutes"])
        self.assertEqual(2, len(detail["cast"]))
        self.assertEqual(("Actor One", "Hero"), (
            detail["cast"][0]["person_name"], detail["cast"][0]["character_name"]
        ))
        self.assertEqual(1, len(detail["seasons"]))
        episodes = detail["seasons"][0]["episodes"]
        self.assertEqual(2, len(episodes))
        self.assertEqual("Arrival", episodes[0]["title"])
        self.assertEqual("Episode one overview.", episodes[0]["overview"])
        self.assertEqual(24, episodes[0]["runtime_minutes"])
        self.assertEqual("Return", episodes[1]["title"])
        self.assertEqual(25, episodes[1]["runtime_minutes"])

    def test_metadata_refresh_preserves_prime_ids(self):
        same_series = self.catalog.get_or_create_series(
            english_name="Example Series",
            root_simkl_id="399",
            tvdb_id="999",
            publish_year=2030,
            overview="Updated series overview.",
            runtime_minutes=25,
            air_status="finished",
        )
        same_episode = self.catalog.add_episode(
            self.season["local_id"], 1, source_episode_number=1,
            simkl_id="401", title="Arrival Updated",
            overview="Updated episode overview.", runtime_minutes=26,
            release_date="2030-01-01",
        )
        self.assertEqual(self.series["local_id"], same_series["local_id"])
        self.assertEqual(self.ep1["local_id"], same_episode["local_id"])
        self.assertEqual("Updated series overview.", same_series["overview"])
        self.assertEqual("Arrival Updated", same_episode["title"])

    def test_missing_cast_metadata_does_not_delete_existing_cast(self):
        self.catalog.replace_series_cast(self.series["local_id"], None, source_provider="other")
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual(2, len(detail["cast"]))

    def test_explicit_empty_cast_replaces_previous_cast(self):
        self.catalog.replace_series_cast(self.series["local_id"], [], source_provider="simkl")
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual([], detail["cast"])


if __name__ == "__main__":
    unittest.main()
