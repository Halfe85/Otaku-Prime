import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.services.kodi_db_middleware import KodiDbMiddleware
from resources.lib.services.mediator_service import MediatorService
from resources.lib.services.stream_library import StreamLibraryService


class StreamLibraryServiceTests(unittest.TestCase):
    def test_writes_requested_tv_series_structure_and_stable_url(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = StreamLibraryService(directory)
            path = writer.write_episode(
                {"local_id": 4, "english_name": "Frieren"},
                {
                    "local_episode_id": 42,
                    "season_number": 1,
                    "episode_number": 3,
                    "english_name": "Killing Magic",
                },
            )

            self.assertEqual(
                os.path.join(
                    directory,
                    "tv-series",
                    "Frieren",
                    "season 01",
                    "Frieren - S01E03.strm",
                ),
                path,
            )
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(
                    "plugin://plugin.video.otaku.prime/play/episode/42\n",
                    handle.read(),
                )

    def test_sanitizes_path_components(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = StreamLibraryService(directory)
            path = writer.write_episode(
                {"local_id": 1, "english_name": "Fate/stay night"},
                {
                    "local_episode_id": 2,
                    "season_number": 2,
                    "episode_number": 1,
                    "english_name": "A: B?",
                },
            )
            self.assertIn("Fate_stay night", path)
            self.assertTrue(path.endswith("Fate_stay night - S02E01.strm"))

    def test_creates_two_source_roots_and_flat_movie_file(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = StreamLibraryService(directory)
            writer.initialize()
            self.assertTrue(os.path.isdir(writer.movies_root))
            self.assertTrue(os.path.isdir(writer.tv_series_root))
            path = writer.write_movie({
                "local_id": "movie-id", "english_name": "Movie Title", "year": 2024
            })
            self.assertEqual(
                os.path.join(writer.movies_root, "Movie Title 2024.strm"), path
            )
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(
                    "plugin://plugin.video.otaku.prime/play/movie/movie-id\n",
                    handle.read(),
                )


class KodiDbMiddlewareTests(unittest.TestCase):
    def test_updates_watch_state_and_requests_library_scan_through_json_rpc(self):
        requests = []

        def execute(payload):
            request = json.loads(payload)
            requests.append(request)
            if request["method"] == "Files.GetSources":
                return json.dumps({"jsonrpc": "2.0", "id": request["id"],
                    "result": {"sources": [{"file": "/prime/library"}]}})
            return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": "OK"})

        middleware = KodiDbMiddleware(object(), execute)
        middleware.set_episode_watched(12, True)
        middleware.set_series_watched(11, True)
        middleware.set_movie_watched(13, False)
        middleware.scan("/prime/library")

        self.assertEqual(
            [
                "VideoLibrary.SetEpisodeDetails",
                "VideoLibrary.SetTVShowDetails",
                "VideoLibrary.SetMovieDetails",
                "Files.GetSources",
                "VideoLibrary.Scan",
            ],
            [request["method"] for request in requests],
        )
        self.assertEqual(1, requests[0]["params"]["playcount"])
        self.assertEqual(0, requests[2]["params"]["playcount"])

    def test_scan_refuses_unconfigured_source(self):
        def execute(payload):
            request = json.loads(payload)
            return json.dumps({"jsonrpc": "2.0", "id": request["id"],
                "result": {"sources": []}})
        middleware = KodiDbMiddleware(object(), execute)
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            middleware.scan("/prime/library/tv-series")

    def test_mediator_publishes_files_before_scanning(self):
        events = []

        class Writer:
            library_root = "/prime/library"
            tv_series_root = "/prime/library/tv-series"

            def write_series(self, series, episodes):
                events.append("write")
                return ["episode.strm"]

        class KodiDb:
            def scan(self, path):
                events.append(("scan", path))

        paths = MediatorService(object(), Writer(), KodiDb()).publish_series({}, [{}])
        self.assertEqual(["episode.strm"], paths)
        self.assertEqual(["write", ("scan", "/prime/library/tv-series")], events)

    def test_mediator_updates_sqlite_before_kodi(self):
        events = []

        class Store:
            def set_watch_status(self, media_type, local_id, watched):
                events.append(("sqlite", media_type, local_id, watched))

            def get_kodi_link(self, media_type, local_id):
                return {"kodi_episode_id": 77}

        class KodiDb:
            def set_episode_watched(self, kodi_id, watched):
                events.append(("kodi", kodi_id, watched))

        MediatorService(Store(), object(), KodiDb()).set_watch_status(
            "episode", 12, True
        )
        self.assertEqual(
            [("sqlite", "episode", 12, True), ("kodi", 77, True)], events
        )


if __name__ == "__main__":
    unittest.main()
