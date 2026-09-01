from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ElementTree

from resources.lib.services.runtime_prime_physical import (
    _kodi_refresh_movie,
    _kodi_refresh_tvshow,
    _kodi_video_scan,
)
from resources.lib.services.runtime_prime_physical_movies import (
    RuntimePrimePhysicalMoviesService as RuntimePrimePhysicalService,
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
        self.movies = [{
            "local_id": "123abc",
            "watchlist_local_id": "movie-watchlist",
            "english_name": "Example Movie",
            "romaji_name": "Example Movie",
            "publish_year": 2025,
            "release_date": "2025-07-01",
            "overview": "A standalone Prime movie.",
            "runtime_minutes": 100,
            "anilist_id": "1001",
            "mal_id": "2002",
            "genres_json": '["Action"]',
            "themes_json": "[]",
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

    def list_movies(self):
        return list(self.movies)

    def library_movie_detail(self, movie_id):
        row = next(
            (row for row in self.movies if row["local_id"] == str(movie_id)),
            None,
        )
        return dict(row) if row else None


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

    def test_startup_scans_tv_and_movies_before_and_after_backfill(self):
        result = self.physical.project_all()
        tv_source = os.path.join(self.temporary.name, "TV-Series").replace("\\", "/") + "/"
        movie_source = os.path.join(self.temporary.name, "Movies").replace("\\", "/") + "/"

        self.assertEqual(4, len(self.scans.requests))
        self.assertEqual((tv_source, "prime_startup"), (
            self.scans.requests[0]["path"], self.scans.requests[0]["reason"]
        ))
        self.assertEqual((movie_source, "prime_startup_movies"), (
            self.scans.requests[1]["path"], self.scans.requests[1]["reason"]
        ))
        self.assertEqual((tv_source, "prime_startup_backfill"), (
            self.scans.requests[2]["path"], self.scans.requests[2]["reason"]
        ))
        self.assertEqual((movie_source, "prime_startup_movies_backfill"), (
            self.scans.requests[3]["path"], self.scans.requests[3]["reason"]
        ))
        self.assertTrue(result["startup_scan"]["queued"])
        self.assertTrue(result["startup_movie_scan"]["queued"])
        self.assertTrue(result["final_scan"]["queued"])
        self.assertTrue(result["final_movie_scan"]["queued"])

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

    def test_direct_movie_projection_uses_requested_movie_folder_and_scoped_scan(self):
        self.physical.project_all()
        self.scans.requests.clear()

        result = self.physical.project_movie("123abc")

        directory = os.path.join(self.temporary.name, "Movies", "Example Movie 2025")
        strm = os.path.join(directory, "Example Movie 2025.strm")
        nfo = os.path.join(directory, "Example Movie 2025.nfo")
        self.assertTrue(os.path.isfile(strm))
        self.assertTrue(os.path.isfile(nfo))
        with open(strm, "r", encoding="utf-8") as handle:
            self.assertEqual(
                "plugin://plugin.video.otaku.prime/play/library/123abc\n",
                handle.read(),
            )
        movie = ElementTree.parse(nfo).getroot()
        self.assertEqual("movie", movie.tag)
        self.assertEqual("Example Movie", movie.findtext("title"))
        self.assertEqual("2025", movie.findtext("year"))
        self.assertEqual("123abc", movie.find("uniqueid[@type='prime']").text)
        self.assertEqual(1, len(self.scans.requests))
        self.assertEqual("mediator_movie", self.scans.requests[0]["reason"])
        self.assertTrue(self.scans.requests[0]["path"].endswith("/Movies/Example Movie 2025/"))
        self.assertTrue(result["scan"]["queued"])

    def test_kodi_configuration_registers_movies_as_recursive_local_information_source(self):
        database = os.path.join(self.temporary.name, "MyVideos999.db")
        with sqlite3.connect(database) as db:
            db.execute("""CREATE TABLE path(
              idPath INTEGER PRIMARY KEY AUTOINCREMENT,
              strPath TEXT,strContent TEXT,strScraper TEXT,strHash TEXT,
              scanRecursive INTEGER,useFolderNames INTEGER,strSettings TEXT,
              noUpdate INTEGER,exclude INTEGER,allAudio INTEGER)""")

        root = self.temporary.name
        tv_source = os.path.join(root, "TV-Series").replace("\\", "/") + "/"
        movie_source = os.path.join(root, "Movies").replace("\\", "/") + "/"
        physical = RuntimePrimePhysicalService(
            self.catalog,
            root_path=root,
            scan_queue=FakeScanQueue(),
            video_database_path=database,
            runtime_video_sources=lambda: [tv_source, movie_source],
            now=lambda: 1798761600,
        )
        result = physical.ensure_kodi_library_configuration()

        self.assertTrue(result["movies"]["source"]["configured"])
        self.assertTrue(result["movies"]["content"]["configured"])
        self.assertEqual(1, result["movies"]["content"]["scan_recursive"])
        self.assertEqual(1, result["movies"]["content"]["use_folder_names"])
        sources = ElementTree.parse(os.path.join(root, "sources.xml")).getroot()
        entries = {
            node.findtext("name"): node.findtext("path")
            for node in sources.find("video").findall("source")
        }
        self.assertEqual(tv_source, entries["Otaku Prime TV-Series"])
        self.assertEqual(movie_source, entries["Otaku Prime Movies"])
        with sqlite3.connect(database) as db:
            row = db.execute(
                "SELECT strContent,strScraper,scanRecursive,useFolderNames "
                "FROM path WHERE strPath=?",
                (movie_source,),
            ).fetchone()
        self.assertEqual(("movies", "metadata.local", 1, 1), row)

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

    def test_kodi_refresh_matches_movie_parent_folder_and_reloads_local_nfo(self):
        calls = []
        xbmc = types.SimpleNamespace()

        def execute_jsonrpc(payload):
            request = json.loads(payload)
            calls.append(request)
            if request["method"] == "VideoLibrary.GetMovies":
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "movies": [{
                            "movieid": 84,
                            "file": "/prime/library/Movies/Example Movie 2025/Example Movie 2025.strm",
                        }]
                    },
                })
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "OK"})

        xbmc.executeJSONRPC = execute_jsonrpc
        previous = sys.modules.get("xbmc")
        sys.modules["xbmc"] = xbmc
        try:
            result = _kodi_refresh_movie(
                "/prime/library/Movies/Example Movie 2025"
            )
        finally:
            if previous is None:
                sys.modules.pop("xbmc", None)
            else:
                sys.modules["xbmc"] = previous

        self.assertTrue(result["refreshed"])
        self.assertEqual(84, result["movieid"])
        self.assertEqual("VideoLibrary.GetMovies", calls[0]["method"])
        self.assertEqual("VideoLibrary.RefreshMovie", calls[1]["method"])
        self.assertEqual({
            "movieid": 84,
            "ignorenfo": False,
        }, calls[1]["params"])


if __name__ == "__main__":
    unittest.main()
