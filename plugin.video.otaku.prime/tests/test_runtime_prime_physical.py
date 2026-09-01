from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

from resources.lib.services.runtime_prime_physical import (
    RuntimePrimePhysicalService,
    _kodi_refresh_tvshow,
    _kodi_video_scan,
)


class FakeCatalog:
    def __init__(self):
        self.series = [{
            "local_id": "abcdef",
            "english_name": "Example Show",
            "romaji_name": "Example Show",
            "publish_year": 2026,
        }]
        self.seasons = [{
            "local_id": "abcdef000001",
            "related_series_id": "abcdef",
            "season_number": 1,
            "release_date": "2026-01-01",
        }]
        self.episodes = [{
            "local_id": "abcdef000001000001",
            "episode_number": 1,
            "release_date": "2026-01-01",
        }]

    def list_series(self):
        return list(self.series)

    def get_series(self, series_id):
        return next(
            (row for row in self.series if row["local_id"] == str(series_id)),
            None,
        )

    def list_seasons(self, series_id):
        return [
            row for row in self.seasons
            if row["related_series_id"] == str(series_id)
        ]

    def list_episodes(self, season_id):
        return list(self.episodes) if str(season_id) == "abcdef000001" else []


class FakeScanQueue:
    def __init__(self):
        self.requests = []

    def request(self, directory, reason="prime_physical"):
        path = str(directory).replace("\\", "/")
        if not path.endswith("/"):
            path += "/"
        row = {"queued": True, "path": path, "reason": reason}
        self.requests.append(row)
        return dict(row)


class RuntimePrimePhysicalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = FakeCatalog()
        self.scans = FakeScanQueue()
        self.physical = RuntimePrimePhysicalService(
            self.catalog,
            root_path=self.temporary.name,
            scan_queue=self.scans,
            now=lambda: 1798761600,  # 2027-01-01 UTC
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_startup_starts_root_scan_and_finishes_with_root_reconcile_scan(self):
        result = self.physical.project_all()
        source = os.path.join(self.temporary.name, "TV-Series").replace("\\", "/") + "/"

        self.assertEqual(2, len(self.scans.requests))
        self.assertEqual(source, self.scans.requests[0]["path"])
        self.assertEqual("prime_startup", self.scans.requests[0]["reason"])
        self.assertEqual(source, self.scans.requests[1]["path"])
        self.assertEqual("prime_startup_backfill", self.scans.requests[1]["reason"])
        self.assertTrue(result["startup_scan"]["queued"])
        self.assertTrue(result["final_scan"]["queued"])

    def test_direct_series_projection_requests_only_that_series_directory(self):
        self.physical.project_all()
        self.scans.requests.clear()

        result = self.physical.project_series("abcdef")

        self.assertEqual(1, len(self.scans.requests))
        self.assertEqual("mediator_series", self.scans.requests[0]["reason"])
        self.assertTrue(
            self.scans.requests[0]["path"].endswith(
                "/TV-Series/Example Show 2026/"
            )
        )
        self.assertTrue(result["scan"]["queued"])

        strm = os.path.join(
            self.temporary.name,
            "TV-Series",
            "Example Show 2026",
            "Season 01",
            "Example Show - S01E01.strm",
        )
        with open(strm, "r", encoding="utf-8") as handle:
            self.assertEqual(
                "plugin://plugin.video.otaku.prime/play/library/abcdef000001000001\n",
                handle.read(),
            )

    def test_kodi_scan_uses_directory_scoped_hidden_video_library_scan(self):
        calls = []
        xbmc = types.SimpleNamespace()

        def execute_jsonrpc(payload):
            calls.append(json.loads(payload))
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "OK"})

        xbmc.executeJSONRPC = execute_jsonrpc
        previous = sys.modules.get("xbmc")
        sys.modules["xbmc"] = xbmc
        try:
            result = _kodi_video_scan("/prime/library/TV-Series/Example Show 2026")
        finally:
            if previous is None:
                sys.modules.pop("xbmc", None)
            else:
                sys.modules["xbmc"] = previous

        self.assertEqual("OK", result)
        self.assertEqual(1, len(calls))
        self.assertEqual("VideoLibrary.Scan", calls[0]["method"])
        self.assertEqual({
            "directory": "/prime/library/TV-Series/Example Show 2026/",
            "showdialogs": False,
        }, calls[0]["params"])

    def test_kodi_refresh_matches_show_folder_and_reloads_local_nfos(self):
        calls = []
        xbmc = types.SimpleNamespace()

        def execute_jsonrpc(payload):
            request = json.loads(payload)
            calls.append(request)
            if request["method"] == "VideoLibrary.GetTVShows":
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tvshows": [{
                            "tvshowid": 42,
                            "file": "/prime/library/TV-Series/Example Show 2026/",
                        }]
                    },
                })
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "OK"})

        xbmc.executeJSONRPC = execute_jsonrpc
        previous = sys.modules.get("xbmc")
        sys.modules["xbmc"] = xbmc
        try:
            result = _kodi_refresh_tvshow(
                "/prime/library/TV-Series/Example Show 2026"
            )
        finally:
            if previous is None:
                sys.modules.pop("xbmc", None)
            else:
                sys.modules["xbmc"] = previous

        self.assertTrue(result["refreshed"])
        self.assertEqual(42, result["tvshowid"])
        self.assertEqual("VideoLibrary.GetTVShows", calls[0]["method"])
        self.assertEqual("VideoLibrary.RefreshTVShow", calls[1]["method"])
        self.assertEqual({
            "tvshowid": 42,
            "ignorenfo": False,
            "refreshepisodes": True,
        }, calls[1]["params"])


if __name__ == "__main__":
    unittest.main()
