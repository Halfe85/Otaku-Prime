import os
import tempfile
import unittest

from resources.lib.services.prime_strm import PLUGIN_BASE, PrimeStrmWriter


class FakeCatalog:
    def __init__(self):
        self.series = {
            "local_id": "abcdef",
            "english_name": "Attack on Titan",
            "publish_year": 2013,
        }
        self.seasons = [{
            "local_id": "abcdef000001",
            "related_series_id": "abcdef",
            "season_number": 1,
        }]
        self.episodes = [{
            "local_id": "abcdef000001000001",
            "related_season_id": "abcdef000001",
            "episode_number": 1,
            "release_date": "2013-04-07",
        }]

    def get_series(self, series_id):
        return dict(self.series) if str(series_id) == "abcdef" else None

    def list_seasons(self, series_id):
        return [dict(row) for row in self.seasons]

    def list_episodes(self, season_id):
        return [dict(row) for row in self.episodes]


class PrimeStrmWriterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.series_directory = os.path.join(
            self.temporary.name, "Attack on Titan 2013"
        )
        self.season_directory = os.path.join(self.series_directory, "Season 01")
        os.makedirs(self.season_directory)
        self.target = os.path.join(
            self.season_directory, "Attack on Titan - S01E01.strm"
        )
        self.catalog = FakeCatalog()

    def tearDown(self):
        self.temporary.cleanup()

    def test_replaces_existing_zero_byte_placeholder_with_plugin_url(self):
        with open(self.target, "wb"):
            pass

        result = PrimeStrmWriter(self.catalog).write_series(
            "abcdef", self.series_directory, now_epoch=1798761600
        )

        self.assertEqual(1, result["written"])
        with open(self.target, "r", encoding="utf-8") as handle:
            self.assertEqual(
                PLUGIN_BASE + "abcdef000001000001\n",
                handle.read(),
            )

    def test_playable_strm_projection_is_idempotent(self):
        writer = PrimeStrmWriter(self.catalog)
        first = writer.write_series(
            "abcdef", self.series_directory, now_epoch=1798761600
        )
        second = writer.write_series(
            "abcdef", self.series_directory, now_epoch=1798761600
        )

        self.assertEqual(1, first["written"])
        self.assertEqual(0, second["written"])
        self.assertEqual(1, second["unchanged"])


if __name__ == "__main__":
    unittest.main()
