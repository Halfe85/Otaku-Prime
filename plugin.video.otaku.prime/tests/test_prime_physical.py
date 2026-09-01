import os
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

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

    def test_video_source_is_added_without_removing_existing_sources(self):
        sources_path = os.path.join(self.temporary.name, "kodi-sources.xml")
        with open(sources_path, "w", encoding="utf-8") as handle:
            handle.write(
                "<sources><video><default pathversion='1'></default>"
                "<source><name>Local videos</name><path pathversion='1'>"
                "/media/videos/</path></source></video>"
                "<music><source><name>Music</name><path>/media/music/</path>"
                "</source></music></sources>"
            )
        physical = PrimePhysicalService(
            self.catalog, root_path=self.temporary.name,
            sources_path=sources_path, source_url="/prime/Library/TV-Series/",
            now=lambda: 1767225600,
        )

        first = physical.ensure_video_source()
        second = physical.ensure_video_source()
        document = ElementTree.parse(sources_path)
        video_sources = document.getroot().find("video").findall("source")

        self.assertTrue(first["configured"])
        self.assertTrue(first["changed"])
        self.assertEqual(first, second)
        self.assertEqual(["Local videos", "Otaku Prime TV-Series"], [
            source.findtext("name") for source in video_sources
        ])
        self.assertEqual("/media/music/", document.findtext("music/source/path"))
        self.assertEqual(
            "/prime/Library/TV-Series/", video_sources[1].findtext("path")
        )

    def test_video_source_registration_is_idempotent_across_instances(self):
        sources_path = os.path.join(self.temporary.name, "kodi-sources.xml")
        first = PrimePhysicalService(
            self.catalog, root_path=self.temporary.name,
            sources_path=sources_path, source_url="/prime/Library/TV-Series/",
        )
        second = PrimePhysicalService(
            self.catalog, root_path=self.temporary.name,
            sources_path=sources_path, source_url="/prime/Library/TV-Series/",
        )

        self.assertTrue(first.ensure_video_source()["changed"])
        self.assertFalse(second.ensure_video_source()["changed"])
        document = ElementTree.parse(sources_path)
        self.assertEqual(1, len(document.getroot().find("video").findall("source")))

    def test_new_source_reports_restart_until_kodi_loads_it(self):
        sources_path = os.path.join(self.temporary.name, "kodi-sources.xml")
        notifications = []
        physical = PrimePhysicalService(
            self.catalog,
            root_path=self.temporary.name,
            sources_path=sources_path,
            source_url="/prime/Library/TV-Series/",
            runtime_video_sources=lambda: [],
            notify_source_restart=lambda: notifications.append(True),
        )

        result = physical.ensure_video_source()

        self.assertTrue(result["configured"])
        self.assertFalse(result["active"])
        self.assertTrue(result["restart_required"])
        self.assertEqual([True], notifications)

    def test_existing_source_is_confirmed_against_kodi_runtime(self):
        sources_path = os.path.join(self.temporary.name, "kodi-sources.xml")
        first = PrimePhysicalService(
            self.catalog,
            root_path=self.temporary.name,
            sources_path=sources_path,
            source_url="/prime/Library/TV-Series/",
            runtime_video_sources=lambda: [],
            notify_source_restart=lambda: None,
        )
        first.ensure_video_source()
        notifications = []
        running = PrimePhysicalService(
            self.catalog,
            root_path=self.temporary.name,
            sources_path=sources_path,
            source_url="/prime/Library/TV-Series/",
            runtime_video_sources=lambda: ["/prime/Library/TV-Series/"],
            notify_source_restart=lambda: notifications.append(True),
        )

        result = running.ensure_video_source()

        self.assertTrue(result["active"])
        self.assertFalse(result["restart_required"])
        self.assertEqual([], notifications)

    def test_malformed_sources_file_is_preserved(self):
        sources_path = os.path.join(self.temporary.name, "kodi-sources.xml")
        payload = b"<sources><video>"
        with open(sources_path, "wb") as handle:
            handle.write(payload)
        physical = PrimePhysicalService(
            self.catalog, root_path=self.temporary.name,
            sources_path=sources_path,
        )

        result = physical.ensure_video_source()

        self.assertFalse(result["configured"])
        with open(sources_path, "rb") as handle:
            self.assertEqual(payload, handle.read())


if __name__ == "__main__":
    unittest.main()
