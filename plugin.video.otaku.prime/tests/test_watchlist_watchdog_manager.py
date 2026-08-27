# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from resources.lib.services.watchlist_watchdog import (
    WATCHLIST_ADDED,
    WATCHLIST_REMOVED,
    WATCHLIST_UPDATED,
    WatchlistWatchdogService,
    WatchlistWatchdogStore,
)


class _Writer:
    def __init__(self):
        self.calls = []

    def push(self, provider, item, entry):
        self.calls.append((provider, item["local_id"], item["status"], item["progress"]))
        return {"updated_at": "2030-01-01T00:00:00Z"}


class WatchlistWatchdogManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "users.sqlite")
        self.store = WatchlistWatchdogStore(self.db_path)
        self.store.initialize()
        self.store.replace_provider_snapshot("anilist", [{
            "provider_item_id": "101506",
            "ids": {"anilist": "101506"},
            "english_name": "UzaMaid!",
            "romaji_name": "Uchi no Maid ga Uzasugiru!",
            "native_name": None,
            "list_status": "CURRENT",
            "provider_status": "CURRENT",
            "progress": 1,
            "episode_count": 12,
            "media_format": "TV",
            "release_date": "2018-10-05",
            "provider_updated_at": "2026-08-27T04:00:00Z",
            "is_adult": False,
            "raw": {"id": 101506},
        }])
        self.store.finalize_merge()
        self.writer = _Writer()
        self.manager = WatchlistWatchdogService([], self.store, self.writer)
        self.item = self.store.list_all()[0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_rejects_program_master_writes(self):
        with self.assertRaises(RuntimeError):
            self.store.set_master_state(self.item["local_id"], "CURRENT", 2)

    def test_manager_updates_prime_and_emits_event(self):
        events = []
        self.manager.subscribe(events.append)

        result = self.manager.update_item(
            self.item["local_id"], progress=2, source="kodi-playback"
        )

        self.assertTrue(result["changed"])
        saved = self.store.item(self.item["local_id"])
        self.assertEqual(saved["progress"], 2)
        self.assertEqual(saved["master_updated_source"], "prime")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], WATCHLIST_UPDATED)
        self.assertEqual(events[0]["local_id"], self.item["local_id"])
        self.assertEqual(events[0]["source"], "kodi-playback")
        self.assertIn("progress", events[0]["changed_fields"])
        self.assertEqual(events[0]["previous"]["progress"], 1)
        self.assertEqual(events[0]["item"]["progress"], 2)

    def test_no_event_for_noop_manager_update(self):
        events = []
        self.manager.subscribe(events.append)
        result = self.manager.update_item(
            self.item["local_id"], status="CURRENT", progress=1, source="web-ui"
        )
        self.assertFalse(result["changed"])
        self.assertEqual(events, [])

    def test_remote_diff_emits_added_updated_and_removed(self):
        events = []
        self.manager.subscribe(events.append)
        before = {
            "a": {"local_id": "a", "status": "CURRENT", "progress": 1},
            "b": {"local_id": "b", "status": "CURRENT", "progress": 1},
        }
        after = {
            "a": {"local_id": "a", "status": "CURRENT", "progress": 2,
                  "master_updated_source": "mal"},
            "c": {"local_id": "c", "status": "PLANNING", "progress": 0,
                  "master_updated_source": "anilist"},
        }

        self.manager._emit_remote_diff(before, after)

        by_type = {event["type"]: event for event in events}
        self.assertEqual(set(by_type), {WATCHLIST_ADDED, WATCHLIST_UPDATED, WATCHLIST_REMOVED})
        self.assertEqual(by_type[WATCHLIST_UPDATED]["source"], "mal")
        self.assertEqual(by_type[WATCHLIST_ADDED]["source"], "anilist")
        self.assertEqual(by_type[WATCHLIST_REMOVED]["local_id"], "b")


if __name__ == "__main__":
    unittest.main()
