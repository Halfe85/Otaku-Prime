from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from resources.lib.services.physical_library_identity import (
    MEDIA_MOVIE,
    MEDIA_SERIES,
    PLUGIN_BASE,
    PhysicalLibraryIdentityRegistry,
)


TVSHOW_NFO = """<?xml version='1.0' encoding='utf-8'?>
<tvshow><title>{title}</title><uniqueid type="prime" default="true">{prime}</uniqueid></tvshow>
"""
MOVIE_NFO = """<?xml version='1.0' encoding='utf-8'?>
<movie><title>{title}</title><uniqueid type="prime" default="true">{prime}</uniqueid></movie>
"""


class PhysicalLibraryIdentityTests(unittest.TestCase):
    def _registry(self, root):
        library = os.path.join(root, "library")
        os.makedirs(os.path.join(library, "TV-Series"), exist_ok=True)
        os.makedirs(os.path.join(library, "Movies"), exist_ok=True)
        db_path = os.path.join(root, "prime.sqlite")
        sqlite3.connect(db_path).close()
        registry = PhysicalLibraryIdentityRegistry(db_path, library)
        registry.initialize()
        return library, registry

    @staticmethod
    def _write(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value)

    def test_duplicate_tv_folders_with_same_prime_id_are_consolidated(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            old = os.path.join(library, "TV-Series", "Bang Dream 2017")
            desired = os.path.join(library, "TV-Series", "BanG Dream! 2017")
            self._write(
                os.path.join(old, "tvshow.nfo"),
                TVSHOW_NFO.format(title="Bang Dream", prime="b59120"),
            )
            self._write(
                os.path.join(desired, "tvshow.nfo"),
                TVSHOW_NFO.format(title="BanG Dream!", prime="b59120"),
            )
            self._write(
                os.path.join(old, "Season 01", "Bang Dream - S01E01.strm"),
                PLUGIN_BASE + "b591200dff1b64ab37\n",
            )
            self._write(
                os.path.join(desired, "Season 01", "BanG Dream! - S01E01.strm"),
                PLUGIN_BASE + "b591200dff1b64ab37\n",
            )

            result = registry.resolve(MEDIA_SERIES, "b59120", desired)

            self.assertEqual(os.path.abspath(desired), result["directory"])
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.isdir(desired))
            self.assertEqual([os.path.abspath(old)], result["duplicates_removed"])
            self.assertTrue(result["kodi_cleanup_pending"])
            self.assertEqual(
                [os.path.abspath(desired)],
                [row["directory"] for row in registry.discover(MEDIA_SERIES, "b59120")],
            )

    def test_title_change_migrates_existing_prime_owned_directory(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            old = os.path.join(library, "TV-Series", "Bakemonogatari 2009")
            desired = os.path.join(library, "TV-Series", "Bakemonogatari 2012")
            self._write(
                os.path.join(old, "tvshow.nfo"),
                TVSHOW_NFO.format(title="Bakemonogatari", prime="385a56"),
            )

            result = registry.resolve(MEDIA_SERIES, "385a56", desired)

            self.assertEqual(os.path.abspath(desired), result["directory"])
            self.assertEqual([os.path.abspath(old)], result["migrated_from"])
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.isfile(os.path.join(desired, "tvshow.nfo")))
            mapped = registry.mapped(MEDIA_SERIES, "385a56")
            self.assertEqual(os.path.abspath(desired), mapped["directory"])
            self.assertEqual(1, mapped["kodi_cleanup_pending"])

    def test_discovery_works_without_catalogue_rows(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            directory = os.path.join(library, "TV-Series", "Orphaned Prime Show 2020")
            self._write(
                os.path.join(directory, "tvshow.nfo"),
                TVSHOW_NFO.format(title="Orphaned Prime Show", prime="c36f13"),
            )

            discovered = registry.discover(MEDIA_SERIES)

            self.assertEqual(1, len(discovered))
            self.assertEqual("c36f13", discovered[0]["prime_id"])
            self.assertEqual(os.path.abspath(directory), discovered[0]["directory"])

    def test_tv_discovery_falls_back_to_episode_strm_prime_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            directory = os.path.join(library, "TV-Series", "No NFO Yet")
            self._write(
                os.path.join(directory, "Season 01", "Show - S01E01.strm"),
                PLUGIN_BASE + "abcdef123456789012\n",
            )

            discovered = registry.discover(MEDIA_SERIES, "abcdef")

            self.assertEqual(1, len(discovered))
            self.assertEqual(os.path.abspath(directory), discovered[0]["directory"])

    def test_prune_series_files_removes_old_title_strm_and_nfo(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            directory = os.path.join(library, "TV-Series", "BanG Dream! 2017")
            old_strm = os.path.join(directory, "Season 01", "Bang Dream - S01E01.strm")
            old_nfo = os.path.splitext(old_strm)[0] + ".nfo"
            new_strm = os.path.join(directory, "Season 01", "BanG Dream! - S01E01.strm")
            self._write(old_strm, PLUGIN_BASE + "b591200dff1b64ab37\n")
            self._write(old_nfo, "<episodedetails></episodedetails>\n")
            self._write(new_strm, PLUGIN_BASE + "b591200dff1b64ab37\n")

            removed = registry.prune_series_files(directory, [new_strm])

            self.assertIn(os.path.abspath(old_strm), removed)
            self.assertIn(os.path.abspath(old_nfo), removed)
            self.assertFalse(os.path.exists(old_strm))
            self.assertFalse(os.path.exists(old_nfo))
            self.assertTrue(os.path.exists(new_strm))

    def test_movie_folder_rename_uses_prime_id_not_title(self):
        with tempfile.TemporaryDirectory() as root:
            library, registry = self._registry(root)
            old = os.path.join(library, "Movies", "Old Movie Name 2024")
            desired = os.path.join(library, "Movies", "Correct Movie Name 2024")
            self._write(
                os.path.join(old, "Old Movie Name 2024.nfo"),
                MOVIE_NFO.format(title="Old Movie Name", prime="123abc"),
            )
            self._write(
                os.path.join(old, "Old Movie Name 2024.strm"),
                PLUGIN_BASE + "123abc\n",
            )

            result = registry.resolve(MEDIA_MOVIE, "123abc", desired)

            self.assertEqual(os.path.abspath(desired), result["directory"])
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.isdir(desired))
            self.assertTrue(result["kodi_cleanup_pending"])


if __name__ == "__main__":
    unittest.main()
