from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

from resources.lib.services.kodi_scan_reliable import (
    ReliableKodiVideoLibraryScanQueue,
)
from resources.lib.services.kodi_scan_verify_prime import (
    verify_prime_movie,
    verify_prime_series,
)
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService


class FakeNotifications:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1

    def started(self):
        return True

    def finished(self):
        return True


class ReliableKodiScanTests(unittest.TestCase):
    def test_stop_discards_pending_scans_and_rejects_new_work(self):
        queue = ReliableKodiVideoLibraryScanQueue(
            execute_scan=lambda path: "OK", scan_active=lambda:False)
        queue._pending=[("/prime/old/","old")]
        self.assertTrue(queue.stop(timeout=0))
        self.assertEqual([],queue._pending)
        result=queue.request("/prime/new/",reason="shutdown-test")
        self.assertFalse(result["queued"])
        self.assertEqual("service_stopping",result["reason"])

    def test_physical_scan_refreshes_parent_and_target_through_kodi_vfs(self):
        opened = []
        deleted = []

        class File:
            def __init__(self, path, mode):
                opened.append((path, mode))

            def write(self, value):
                return len(value)

            def close(self):
                return None

        fake_vfs = types.SimpleNamespace(
            File=File,
            delete=lambda path: deleted.append(path) or True,
        )

        class Queue:
            def request(self, directory, reason):
                return {"queued": True, "path": directory, "reason": reason}

        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "TV-Series", "Bleach 2004")
            os.makedirs(target)
            service = object.__new__(RuntimePrimePhysicalService)
            service._scan_queue = Queue()
            previous = sys.modules.get("xbmcvfs")
            sys.modules["xbmcvfs"] = fake_vfs
            try:
                result = service.request_kodi_scan(target, reason="mediator_series")
            finally:
                if previous is None:
                    sys.modules.pop("xbmcvfs", None)
                else:
                    sys.modules["xbmcvfs"] = previous

        self.assertTrue(result["queued"])
        self.assertEqual(2, len(opened))
        self.assertEqual(2, len(deleted))
        self.assertTrue(all(path.endswith(".otaku-prime-vfs-refresh") for path, _ in opened))

    def test_notification_lifecycle_and_verified_series_use_one_scan(self):
        calls = []
        notifications = FakeNotifications()
        queue = ReliableKodiVideoLibraryScanQueue(
            execute_scan=lambda path: calls.append(path) or "OK",
            scan_active=lambda: False,
            refresh_series=lambda path: {"refreshed": True, "result": "OK"},
            notification_monitor=notifications,
            verify_series=lambda path: {
                "complete": True,
                "reason": "complete",
                "expected": 1,
                "known": 1,
                "missing": [],
            },
            sleep=lambda seconds: None,
        )

        result = queue._process_scan("/prime/TV-Series/Example", "mediator_series")

        self.assertTrue(result["completed"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, notifications.resets)

    def test_missing_prime_id_retries_exactly_once(self):
        calls = []
        verification_calls = []
        notifications = FakeNotifications()

        def verify(_path):
            verification_calls.append(True)
            complete = len(verification_calls) >= 2
            return {
                "complete": complete,
                "reason": "complete" if complete else "prime_episode_ids_missing",
                "expected": 1,
                "known": 1 if complete else 0,
                "missing": [] if complete else ["abcdef000001000001"],
            }

        queue = ReliableKodiVideoLibraryScanQueue(
            execute_scan=lambda path: calls.append(path) or "OK",
            scan_active=lambda: False,
            notification_monitor=notifications,
            verify_series=verify,
            sleep=lambda seconds: None,
        )

        result = queue._process_scan("/prime/TV-Series/Example", "mediator_series")

        self.assertTrue(result["completed"])
        self.assertEqual(2, len(calls))
        self.assertEqual(2, len(verification_calls))
        self.assertEqual(2, notifications.resets)

    def test_series_verification_uses_prime_episode_uniqueid(self):
        with tempfile.TemporaryDirectory() as root:
            season = os.path.join(root, "Season 01")
            os.makedirs(season)
            with open(os.path.join(season, "Example - S01E01.strm"), "w", encoding="utf-8") as handle:
                handle.write(
                    "plugin://plugin.video.otaku.prime/play/library/abcdef000001000001\n"
                )

            xbmc = types.SimpleNamespace()

            def execute_jsonrpc(payload):
                request = json.loads(payload)
                if request["method"] == "VideoLibrary.GetTVShows":
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "tvshows": [{
                                "tvshowid": 42,
                                "file": root.replace("\\", "/") + "/",
                            }]
                        },
                    })
                if request["method"] == "VideoLibrary.GetEpisodes":
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "episodes": [{
                                "episodeid": 84,
                                "file": "plugin://some/resolved/playback/url",
                                "uniqueid": {"prime": "abcdef000001000001"},
                            }]
                        },
                    })
                raise AssertionError(request["method"])

            xbmc.executeJSONRPC = execute_jsonrpc
            previous = sys.modules.get("xbmc")
            sys.modules["xbmc"] = xbmc
            try:
                result = verify_prime_series(root)
            finally:
                if previous is None:
                    sys.modules.pop("xbmc", None)
                else:
                    sys.modules["xbmc"] = previous

            self.assertTrue(result["complete"])
            self.assertEqual(1, result["expected"])
            self.assertEqual(1, result["known"])
            self.assertEqual([], result["missing"])

    def test_movie_verification_uses_prime_movie_uniqueid(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "Example Movie 2026.strm"), "w", encoding="utf-8") as handle:
                handle.write(
                    "plugin://plugin.video.otaku.prime/play/library/123abc\n"
                )

            xbmc = types.SimpleNamespace()
            xbmc.executeJSONRPC = lambda payload: json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "movies": [{
                        "movieid": 9,
                        "uniqueid": {"prime": "123abc"},
                    }]
                },
            })
            previous = sys.modules.get("xbmc")
            sys.modules["xbmc"] = xbmc
            try:
                result = verify_prime_movie(root)
            finally:
                if previous is None:
                    sys.modules.pop("xbmc", None)
                else:
                    sys.modules["xbmc"] = previous

            self.assertTrue(result["complete"])
            self.assertEqual(9, result["movieid"])


if __name__ == "__main__":
    unittest.main()
