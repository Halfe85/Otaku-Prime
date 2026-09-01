# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
import time
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


class _TransientFailingWriter(_Writer):
    def push(self, provider, item, entry):
        self.calls.append((provider, item["local_id"], item["status"], item["progress"]))
        error=RuntimeError("temporary provider failure")
        error.retryable=True
        error.retry_after=90
        raise error


class _ProviderProjectionWriter(_Writer):
    @staticmethod
    def target_state(provider,item,entry):
        return {"status":item["status"],"progress":min(
            int(item["progress"]),int(entry["episode_count"]))}


class _LifecycleComponent:
    def __init__(self): self.stop_requested=False; self.starts=0; self._thread=None
    def request_stop(self): self.stop_requested=True
    def start(self): self.starts+=1
    def stop(self,timeout=None): self.stop_requested=True; return True


class _LifecycleWriter(_Writer):
    def __init__(self): super().__init__(); self.event=None; self.stop_requested=False
    def set_halt_event(self,event): self.event=event
    def request_stop(self): self.stop_requested=True; self.event.set()


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

    def test_pause_retires_all_producers_and_rejects_new_work(self):
        writer=_LifecycleWriter(); identity=_LifecycleComponent(); mediator=_LifecycleComponent()
        manager=WatchlistWatchdogService(
            [],self.store,writer,identity_enricher=identity,mediator=mediator)

        result=manager.pause()

        self.assertTrue(result["paused"])
        self.assertTrue(writer.event.is_set())
        self.assertTrue(writer.stop_requested)
        self.assertTrue(identity.stop_requested)
        self.assertTrue(mediator.stop_requested)
        self.assertEqual({"scheduled":False,"paused":True},manager.request_remote_sync())
        self.assertEqual({"scheduled":False,"paused":True},manager.identity_complete())
        self.assertEqual(0,mediator.starts)

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

    def test_account_change_schedules_remote_fetch_without_web_callback(self):
        class Accounts:
            def __init__(self):
                self.updated_at = None

            def get(self, user_id, provider):
                if self.updated_at is None:
                    return None
                return {"updated_at": self.updated_at}

        class Importer:
            provider = "anilist"
            user_id = 1

            def __init__(self):
                self.accounts = Accounts()

        importer = Importer()
        manager = WatchlistWatchdogService([importer], self.store, self.writer)
        self.assertFalse(manager._detect_account_change())
        importer.accounts.updated_at = "2030-01-01 00:00:00"
        manager._last_account_check_monotonic = 0.0
        self.assertTrue(manager._detect_account_change())
        self.assertTrue(manager._remote_requested.is_set())

    def test_transient_failure_starts_provider_wide_cooldown(self):
        writer=_TransientFailingWriter()
        manager=WatchlistWatchdogService([],self.store,writer)
        item=self.store.item(self.item["local_id"])
        item["progress"]=2

        manager._sync_master_to_providers(item)
        manager._sync_master_to_providers(item)

        self.assertEqual(1,len(writer.calls))
        self.assertGreater(manager._provider_retry_after["anilist"],time.time()+80)

    def test_provider_equivalent_progress_does_not_repeat_write(self):
        self.store.replace_provider_snapshot("kitsu",[{
            "provider_item_id":"1606",
            "ids":{"anilist":"101506","kitsu":"1606"},
            "english_name":"UzaMaid!","romaji_name":"Uchi no Maid ga Uzasugiru!",
            "native_name":None,"list_status":"COMPLETED","provider_status":"completed",
            "progress":2,"episode_count":2,"media_format":"TV",
            "release_date":"2018-10-05","provider_updated_at":"2026-08-27T04:00:00Z",
            "is_adult":False,"raw":{"id":"1606"},
        }])
        self.store.finalize_merge()
        item=self.store.item(self.item["local_id"])
        item["status"]="COMPLETED"
        item["progress"]=4
        writer=_ProviderProjectionWriter()
        manager=WatchlistWatchdogService([],self.store,writer)

        manager._sync_master_to_providers(item)

        self.assertFalse(any(call[0]=="kitsu" for call in writer.calls))


if __name__ == "__main__":
    unittest.main()
