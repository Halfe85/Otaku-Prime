from __future__ import annotations

import os
import tempfile
import unittest

from resources.lib.database.runtime_catalog import RuntimeCatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


class RuntimeCatalogCollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.sqlite")
        self.watchlist = WatchlistItemStore(self.path)
        self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist", [{
            "provider_item_id": "100",
            "ids": {"anilist": "100"},
            "english_name": "Season Part One",
            "list_status": "CURRENT",
            "progress": 0,
            "episode_count": 1,
            "media_format": "TV",
            "raw": {},
        }])
        self.watchlist.replace_provider_snapshot("mal", [{
            "provider_item_id": "200",
            "ids": {"mal": "200"},
            "english_name": "Season Part Two",
            "list_status": "CURRENT",
            "progress": 0,
            "episode_count": 2,
            "media_format": "TV",
            "raw": {},
        }])
        self.watchlist.finalize_merge()
        rows = self.watchlist.list_all()
        self.first_item = next(row for row in rows if row["anilist_id"] == "100")
        self.second_item = next(row for row in rows if row["mal_id"] == "200")

        self.catalog = RuntimeCatalogStore(self.path, SegmentFactory())
        self.catalog.initialize()
        self.series = self.catalog.get_or_create_series(
            english_name="Shared Season", root_anilist_id="999"
        )
        self.first_season = self.catalog.add_watchlist_season(
            self.series["local_id"], self.first_item, season_number=1
        )
        self.second_season = self.catalog.add_watchlist_season(
            self.series["local_id"], self.second_item, season_number=1
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_distinct_watchlist_part_is_appended_after_occupied_coordinate(self):
        first = self.catalog.add_episode(
            self.first_season["local_id"], 1,
            source_episode_number=1,
            watchlist_local_id=self.first_item["local_id"],
            title="Part one",
        )
        second_one = self.catalog.add_episode(
            self.second_season["local_id"], 1,
            source_episode_number=1,
            watchlist_local_id=self.second_item["local_id"],
            title="Part two episode one",
        )
        second_two = self.catalog.add_episode(
            self.second_season["local_id"], 2,
            source_episode_number=2,
            watchlist_local_id=self.second_item["local_id"],
            title="Part two episode two",
        )

        self.assertEqual(self.first_season["local_id"], self.second_season["local_id"])
        self.assertEqual((1, 2, 3), (
            first["episode_number"],
            second_one["episode_number"],
            second_two["episode_number"],
        ))
        rows = self.catalog.list_episodes(self.first_season["local_id"])
        self.assertEqual([1, 2, 3], [row["episode_number"] for row in rows])
        self.assertEqual([1, 1, 2], [row["source_episode_number"] for row in rows])

    def test_refresh_keeps_previously_allocated_prime_coordinate(self):
        self.catalog.add_episode(
            self.first_season["local_id"], 1,
            source_episode_number=1,
            watchlist_local_id=self.first_item["local_id"],
        )
        inserted = self.catalog.add_episode(
            self.second_season["local_id"], 1,
            source_episode_number=1,
            watchlist_local_id=self.second_item["local_id"],
            title="Initial",
        )
        refreshed = self.catalog.add_episode(
            self.second_season["local_id"], 1,
            source_episode_number=1,
            watchlist_local_id=self.second_item["local_id"],
            title="Refreshed",
        )

        self.assertEqual(2, inserted["episode_number"])
        self.assertEqual(inserted["local_id"], refreshed["local_id"])
        self.assertEqual(2, refreshed["episode_number"])
        self.assertEqual("Refreshed", refreshed["title"])
        self.assertEqual(2, len(self.catalog.list_episodes(self.first_season["local_id"])))


if __name__ == "__main__":
    unittest.main()
