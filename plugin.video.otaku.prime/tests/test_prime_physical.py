import os
import tempfile
import unittest

from resources.lib.services.prime_physical import (
    PrimePhysicalService,
    safe_library_name,
)


class FakeCatalog:
    def __init__(self):
        self.series = [{
            "local_id": "abcdef", "english_name": "Bleach: Final/Arc",
            "romaji_name": "Bleach", "publish_year": 2004,
        }]
        self.seasons = [{
            "local_id": "abcdef000011", "related_series_id": "abcdef",
            "season_number": 17, "release_date": "2022-10-11",
        }]
        self.episodes = [{
            "local_id": "abcdef000011000001", "episode_number": 1,
            "release_date": "2022-10-11",
        }, {
            "local_id": "abcdef000011000002", "episode_number": 2,
            "release_date": "2030-01-01",
        }, {
            "local_id": "abcdef000011000003", "episode_number": 3,
            "release_date": None,
        }]

    def list_series(self):
        return list(self.series)

    def list_seasons(self, series_id):
        return [row for row in self.seasons if row["related_series_id"] == series_id]

    def list_episodes(self, season_id):
        return list(self.episodes) if season_id == "abcdef000011" else []


class PrimePhysicalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = FakeCatalog()
        self.physical = PrimePhysicalService(
            self.catalog, root_path=self.temporary.name,
            now=lambda: 1767225600,  # 2026-01-01 UTC
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_handoff_reads_catalogue_and_creates_only_released_empty_strm(self):
        result = self.physical.project_series("abcdef")
        target = os.path.join(
            self.temporary.name, "TV-Series", "Bleach - Final - Arc 2004",
            "Season 17", "Bleach - Final - Arc - S17E01.strm",
        )

        self.assertTrue(os.path.isfile(target))
        self.assertEqual(0, os.path.getsize(target))
        self.assertEqual(1, result["created"])
        self.assertEqual(1, result["future"])
        self.assertEqual(1, result["unknown_release"])
        self.assertEqual(1, len([
            path for root, _, files in os.walk(self.temporary.name)
            for path in files if path.endswith(".strm")
        ]))

    def test_existing_strm_is_never_truncated(self):
        self.physical.project_series("abcdef")
        target = os.path.join(
            self.temporary.name, "TV-Series", "Bleach - Final - Arc 2004",
            "Season 17", "Bleach - Final - Arc - S17E01.strm",
        )
        with open(target, "wb") as handle:
            handle.write(b"plugin://future-playback")

        result = self.physical.project_series("abcdef")

        with open(target, "rb") as handle:
            self.assertEqual(b"plugin://future-playback", handle.read())
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["existing"])

    def test_date_only_release_waits_until_the_utc_day_has_finished(self):
        self.catalog.episodes = [{
            "local_id": "abcdef000011000001", "episode_number": 1,
            "release_date": "2026-01-01",
        }]
        result = self.physical.project_series("abcdef")
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["future"])

    def test_project_all_backfills_each_existing_series(self):
        result = self.physical.project_all()
        self.assertEqual(1, result["series"])
        self.assertEqual(1, result["created"])

    def test_unknown_series_is_reported_without_creating_directories(self):
        result = self.physical.project_series("ffffff")
        self.assertTrue(result["missing"])
        self.assertFalse(os.path.exists(os.path.join(self.temporary.name, "TV-Series")))

    def test_path_component_is_portable(self):
        self.assertEqual("A - B - C", safe_library_name(" A/B:C. "))


if __name__ == "__main__":
    unittest.main()
