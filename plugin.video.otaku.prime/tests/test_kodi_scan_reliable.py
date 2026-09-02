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
