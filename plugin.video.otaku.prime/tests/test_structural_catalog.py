from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from resources.lib.database.structural_catalog import (
    StructuralCatalogConflict,
    StructuralCatalogStore,
)


class StructuralCatalogTests(unittest.TestCase):
    def _store(self, root):
        path = os.path.join(root, "prime.sqlite")
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE watchlist_items(local_id TEXT PRIMARY KEY)")
            db.executemany(
                "INSERT INTO watchlist_items(local_id) VALUES(?)",
                [("aaaaaa",), ("bbbbbb",), ("cccccc",)],
            )
        store = StructuralCatalogStore(path)
        store.initialize()
        return path, store

    @staticmethod
    def _item(local_id, anilist_id=None, simkl_id=None):
        return {
            "local_id": local_id,
            "anilist_id": anilist_id,
            "mal_id": None,
            "kitsu_id": None,
            "simkl_id": simkl_id,
            "media_format": "SPECIAL",
        }

    def test_conflicting_provider_roots_cannot_cross_tvdb_series(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            first = store.get_or_create_series(
                english_name="BanG Dream!",
                root_simkl_id="111",
                root_anilist_id="87435",
                tvdb_id="320002",
            )
            second = store.get_or_create_series(
                english_name="BanG Dream! It's MyGO!!!!!",
                root_simkl_id="222",
                root_anilist_id="163571",
                tvdb_id="433560",
            )

            resolved = store.get_or_create_series(
                english_name="Wrong relation-root title",
                root_simkl_id="222",
                root_anilist_id="163571",
                tvdb_id="320002",
            )

            self.assertEqual(first["local_id"], resolved["local_id"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                first_row = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (first["local_id"],)
                ).fetchone()
                second_row = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (second["local_id"],)
                ).fetchone()
            self.assertEqual("111", first_row["root_simkl_id"])
            self.assertEqual("87435", first_row["root_anilist_id"])
            self.assertEqual("320002", first_row["tvdb_id"])
            self.assertEqual("222", second_row["root_simkl_id"])
            self.assertEqual("163571", second_row["root_anilist_id"])
            self.assertEqual("433560", second_row["tvdb_id"])

    def test_shared_season_keeps_first_structural_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Monogatari", root_simkl_id="45006", tvdb_id="102261"
            )
            first = self._item("aaaaaa", anilist_id="5081", simkl_id="100")
            second = self._item("bbbbbb", anilist_id="20918", simkl_id="200")

            season = store.add_watchlist_season(
                series["local_id"], first, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=2, english_name="Specials",
            )
            linked = store.add_watchlist_season(
                series["local_id"], second, season_number=0,
                provider_path="anilist", placement_source="anilist_special_format",
                first_episode=15, last_episode=18, english_name="Tsukimonogatari",
            )

            self.assertEqual(season["local_id"], linked["local_id"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM seasons WHERE local_id=?", (season["local_id"],)
                ).fetchone()
                links = db.execute(
                    "SELECT watchlist_local_id FROM season_watchlist_links "
                    "WHERE season_local_id=? ORDER BY watchlist_local_id",
                    (season["local_id"],),
                ).fetchall()
            self.assertEqual("5081", row["anilist_id"])
            self.assertEqual("100", row["simkl_id"])
            self.assertEqual("Specials", row["english_name"])
            self.assertEqual(2, row["first_episode"])
            self.assertEqual(18, row["last_episode"])
            self.assertEqual(["aaaaaa", "bbbbbb"], [value[0] for value in links])

    def test_sparse_special_coordinates_are_never_resequenced(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Monogatari", root_simkl_id="45006", tvdb_id="102261"
            )
            first = self._item("aaaaaa", anilist_id="9260", simkl_id="100")
            second = self._item("bbbbbb", anilist_id="31757", simkl_id="200")
            season = store.add_watchlist_season(
                series["local_id"], first, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=19, english_name="Specials",
            )
            store.add_watchlist_season(
                series["local_id"], second, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=19, english_name="Specials",
            )

            store.add_episode(
                season["local_id"], 19, source_episode_number=1,
                simkl_id="episode-19", watchlist_local_id="bbbbbb",
                title="Kizumonogatari II",
            )
            store.add_episode(
                season["local_id"], 2, source_episode_number=1,
                simkl_id="episode-2", watchlist_local_id="aaaaaa",
                title="Kizumonogatari I",
            )

            with sqlite3.connect(path) as db:
                numbers = [row[0] for row in db.execute(
                    "SELECT episode_number FROM episodes WHERE related_season_id=? "
                    "ORDER BY episode_number", (season["local_id"],)
                )]
            self.assertEqual([2, 19], numbers)

    def test_incompatible_episode_collision_is_rejected_not_appended(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Example", root_simkl_id="999", tvdb_id="12345"
            )
            first = self._item("aaaaaa", anilist_id="1", simkl_id="100")
            second = self._item("bbbbbb", anilist_id="2", simkl_id="200")
            season = store.add_watchlist_season(
                series["local_id"], first, season_number=1,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=1, last_episode=1, english_name="Season 1",
            )
            store.add_watchlist_season(
                series["local_id"], second, season_number=1,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=1, last_episode=1, english_name="Season 1",
            )
            store.add_episode(
                season["local_id"], 1, source_episode_number=1,
                simkl_id="episode-a", watchlist_local_id="aaaaaa",
            )

            with self.assertRaises(StructuralCatalogConflict):
                store.add_episode(
                    season["local_id"], 1, source_episode_number=1,
                    simkl_id="episode-b", watchlist_local_id="bbbbbb",
                )

            with sqlite3.connect(path) as db:
                numbers = [row[0] for row in db.execute(
                    "SELECT episode_number FROM episodes WHERE related_season_id=?",
                    (season["local_id"],),
                )]
            self.assertEqual([1], numbers)


if __name__ == "__main__":
    unittest.main()
