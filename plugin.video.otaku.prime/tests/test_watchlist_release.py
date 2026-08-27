# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from resources.lib.database.catalog import CatalogStore
from resources.lib.services.watchlist_release import (
    WATCHLIST_RELEASE_UPDATED,
    WatchlistReleaseManager,
    release_epoch,
)
from resources.lib.services.watchlist_watchdog import WatchlistWatchdogStore
from resources.lib.services.watchlist_watchdog_release import (
    ReleaseAwareWatchlistWatchdogService,
)


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


class _Writer:
    def push(self, provider, item, entry):
        return {"skipped": True}


class WatchlistReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.sqlite")
        self.store = WatchlistWatchdogStore(self.path)
        self.store.initialize()
        self.store.replace_provider_snapshot("anilist", [{
            "provider_item_id": "100",
            "ids": {"anilist": "100", "simkl": "200"},
            "english_name": "Example Season",
            "romaji_name": "Example Season",
            "list_status": "CURRENT",
            "provider_status": "CURRENT",
            "progress": 1,
            "episode_count": 3,
            "media_format": "TV",
            "release_date": "2030-01-01",
            "provider_updated_at": "2030-01-01T00:00:00Z",
            "raw": {},
        }])
        self.store.finalize_merge()
        self.item = self.store.list_all()[0]
        self.catalog = CatalogStore(self.path, SegmentFactory())
        self.catalog.initialize()
        series = self.catalog.get_or_create_series(
            english_name="Example", root_simkl_id="199", tvdb_id="999"
        )
        self.season = self.catalog.add_watchlist_season(
            series["local_id"], self.item, season_number=1,
            first_episode=1, last_episode=3,
        )
        self.ep1 = self.catalog.add_episode(
            self.season["local_id"], 1, source_episode_number=1,
            simkl_id="201", release_date="2030-01-01",
        )
        self.ep2 = self.catalog.add_episode(
            self.season["local_id"], 2, source_episode_number=2,
            simkl_id="202", release_date="2030-01-08",
        )
        self.ep3 = self.catalog.add_episode(
            self.season["local_id"], 3, source_episode_number=3,
            simkl_id="203", release_date="2030-01-15",
        )
        self.release = WatchlistReleaseManager(self.store)
        self.release.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_schedule_tracks_season_and_next_episode(self):
        events = self.release.refresh_due(
            now_epoch=release_epoch("2030-01-05T12:00:00Z"), force=True
        )
        self.assertEqual(1, len(events))
        item = self.store.item(self.item["local_id"])
        self.assertEqual("2030-01-01", item["season_release_date"])
        self.assertEqual(2, item["next_episode_number"])
        self.assertEqual(2, item["next_source_episode_number"])
        self.assertEqual(self.ep2["local_id"], item["next_episode_local_id"])
        self.assertEqual("2030-01-08", item["next_episode_release_date"])
        self.assertEqual("prime_catalog", item["release_schedule_source"])

    def test_date_only_release_rolls_over_after_release_day(self):
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-05T12:00:00Z"), force=True
        )
        # During January 8 the episode is still the current upcoming release.
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-08T12:00:00Z")
        )
        item = self.store.item(self.item["local_id"])
        self.assertEqual(2, item["next_episode_number"])

        events = self.release.refresh_due(
            now_epoch=release_epoch("2030-01-09T00:00:00Z")
        )
        self.assertEqual(1, len(events))
        item = self.store.item(self.item["local_id"])
        self.assertEqual(3, item["next_episode_number"])
        self.assertEqual("2030-01-15", item["next_episode_release_date"])
        self.assertEqual(2, events[0]["previous"]["next_episode_number"])
        self.assertEqual(3, events[0]["item"]["next_episode_number"])

    def test_schedule_clears_next_episode_after_final_release(self):
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-05T12:00:00Z"), force=True
        )
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-16T00:00:00Z")
        )
        item = self.store.item(self.item["local_id"])
        self.assertIsNone(item["next_episode_number"])
        self.assertIsNone(item["next_episode_release_date"])
        self.assertEqual(0, item["next_episode_release_epoch"])

    def test_watchdog_emits_release_schedule_event(self):
        class FakeReleaseManager:
            def initialize(self):
                pass

            def refresh_due(self, force=False):
                previous = dict(self_item)
                previous["next_episode_number"] = 2
                previous["next_episode_release_epoch"] = 1
                current = dict(self_item)
                current["next_episode_number"] = 3
                current["next_episode_release_date"] = "2030-01-15"
                return [{
                    "local_id": current["local_id"],
                    "previous": previous,
                    "item": current,
                    "changed_fields": [
                        "next_episode_number", "next_episode_release_date"
                    ],
                }]

        self_item = self.store.item(self.item["local_id"])
        watchdog = ReleaseAwareWatchlistWatchdogService(
            [], self.store, _Writer(), release_manager=FakeReleaseManager()
        )
        events = []
        watchdog.subscribe(events.append)
        count = watchdog._process_release_schedules(force=True)
        self.assertEqual(1, count)
        self.assertEqual(1, len(events))
        self.assertEqual(WATCHLIST_RELEASE_UPDATED, events[0]["type"])
        self.assertEqual(self.item["local_id"], events[0]["local_id"])
        self.assertEqual("release-watchdog", events[0]["source"])
        self.assertEqual(2, events[0]["previous"]["next_episode_number"])
        self.assertEqual(3, events[0]["item"]["next_episode_number"])


if __name__ == "__main__":
    unittest.main()
