from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from resources.lib.database.franchise_catalog import FranchiseCatalogStore
from resources.lib.database.structural_catalog import StructuralCatalogConflict
from resources.lib.database.watchlist_items import WatchlistItemStore


class StructuralCatalogTests(unittest.TestCase):
    def _store(self, root):
        path = os.path.join(root, "prime.sqlite")
        watchlist = WatchlistItemStore(path)
        watchlist.initialize()
        with sqlite3.connect(path) as db:
            db.executemany(
                "INSERT INTO watchlist_items(local_id,mediator_ready) VALUES(?,1)",
                [("aaaaaa",), ("bbbbbb",), ("cccccc",)],
            )
        store = FranchiseCatalogStore(path)
        store.initialize()
        return path, store

    @staticmethod
    def _item(local_id, anilist_id=None, simkl_id=None, media_format="SPECIAL"):
        return {
            "local_id": local_id,
            "anilist_id": anilist_id,
            "mal_id": None,
            "kitsu_id": None,
            "simkl_id": simkl_id,
            "media_format": media_format,
        }

    def test_parent_identity_is_immutable_when_target_tvdb_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            first = store.get_or_create_series(
                english_name="BanG Dream!", romaji_name="BanG Dream!",
                root_simkl_id="111", root_anilist_id="87435",
                tvdb_id="320002", publish_year=2017, source_media_format="TV")
            resolved = store.get_or_create_series(
                english_name="BanG Dream! Ave Mujica", romaji_name="Ave Mujica",
                root_simkl_id="111", root_anilist_id="87435",
                tvdb_id="433560", publish_year=2025, source_media_format="TV")

            self.assertEqual(first["local_id"], resolved["local_id"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (first["local_id"],)
                ).fetchone()
            self.assertEqual("BanG Dream!", row["english_name"])
            self.assertEqual("BanG Dream!", row["romaji_name"])
            self.assertEqual("111", row["root_simkl_id"])
            self.assertEqual("87435", row["root_anilist_id"])
            self.assertEqual("320002", row["tvdb_id"])
            self.assertEqual(2017, row["publish_year"])

    def test_structural_tvdb_owner_is_stored_below_franchise(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="BanG Dream!", root_simkl_id="111",
                root_anilist_id="87435", tvdb_id="320002", publish_year=2017)
            item = self._item("aaaaaa", anilist_id="163571", simkl_id="222", media_format="TV")
            season = store.add_watchlist_season(
                series["local_id"], item, season_number=4,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=1, last_episode=13, english_name="It's MyGO!!!!!")
            stored = store.set_watchlist_structural_owner(
                season["local_id"], item["local_id"],
                {"name": "BanG Dream! It's MyGO!!!!!",
                 "simkl_id": "2138098", "tvdb_id": "433560"},
                structural_season_number=1, source_provider="simkl")

            self.assertEqual("433560", stored["structural_tvdb_id"])
            self.assertEqual(1, stored["structural_season_number"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                parent = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (series["local_id"],)
                ).fetchone()
                mapping = db.execute("""SELECT * FROM season_structural_sources
                  WHERE season_local_id=? AND watchlist_local_id=?""",
                    (season["local_id"], item["local_id"]),).fetchone()
            self.assertEqual("BanG Dream!", parent["english_name"])
            self.assertEqual("320002", parent["tvdb_id"])
            self.assertEqual("433560", mapping["structural_tvdb_id"])
            self.assertEqual("2138098", mapping["structural_simkl_id"])

    def test_non_tvdb_special_cannot_fuzzy_merge_and_rename_parent(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            parent = store.get_or_create_series(
                english_name="Bakemonogatari", romaji_name="Bakemonogatari",
                root_simkl_id="45006", root_anilist_id="5081",
                tvdb_id="102261", publish_year=2009)
            pv = store.get_or_create_series(
                english_name="Bakemonogatari PV", romaji_name="Bakemonogatari PV",
                root_simkl_id="999999", root_anilist_id="143663",
                tvdb_id=None, publish_year=2022, source_media_format="SPECIAL")

            self.assertNotEqual(parent["local_id"], pv["local_id"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                parent_row = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (parent["local_id"],)
                ).fetchone()
                pv_row = db.execute(
                    "SELECT * FROM tv_series WHERE local_id=?", (pv["local_id"],)
                ).fetchone()
            self.assertEqual("Bakemonogatari", parent_row["english_name"])
            self.assertEqual("5081", parent_row["root_anilist_id"])
            self.assertEqual(2009, parent_row["publish_year"])
            self.assertEqual("Bakemonogatari PV", pv_row["english_name"])
            self.assertEqual("143663", pv_row["root_anilist_id"])
            self.assertEqual(2022, pv_row["publish_year"])

    def test_shared_special_season_has_no_single_provider_identity(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Monogatari", root_simkl_id="45006", tvdb_id="102261")
            first = self._item("aaaaaa", anilist_id="5081", simkl_id="100")
            second = self._item("bbbbbb", anilist_id="20918", simkl_id="200")

            season = store.add_watchlist_season(
                series["local_id"], first, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=2, english_name="Kizumonogatari")
            linked = store.add_watchlist_season(
                series["local_id"], second, season_number=0,
                provider_path="anilist", placement_source="anilist_special_format",
                first_episode=15, last_episode=18, english_name="Tsukimonogatari")

            self.assertEqual(season["local_id"], linked["local_id"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM seasons WHERE local_id=?",
                                 (season["local_id"],)).fetchone()
                links = db.execute(
                    "SELECT watchlist_local_id FROM season_watchlist_links "
                    "WHERE season_local_id=? ORDER BY watchlist_local_id",
                    (season["local_id"],)).fetchall()
            self.assertIsNone(row["anilist_id"])
            self.assertIsNone(row["simkl_id"])
            self.assertEqual("Specials", row["english_name"])
            self.assertEqual(2, row["first_episode"])
            self.assertEqual(18, row["last_episode"])
            self.assertEqual(["aaaaaa", "bbbbbb"], [value[0] for value in links])

    def test_sparse_special_coordinates_are_never_resequenced(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Monogatari", root_simkl_id="45006", tvdb_id="102261")
            first = self._item("aaaaaa", anilist_id="9260", simkl_id="100")
            second = self._item("bbbbbb", anilist_id="31757", simkl_id="200")
            season = store.add_watchlist_season(
                series["local_id"], first, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=19)
            store.add_watchlist_season(
                series["local_id"], second, season_number=0,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=2, last_episode=19)
            store.add_episode(
                season["local_id"], 19, source_episode_number=1,
                simkl_id="episode-19", watchlist_local_id="bbbbbb",
                title="Kizumonogatari II")
            store.add_episode(
                season["local_id"], 2, source_episode_number=1,
                simkl_id="episode-2", watchlist_local_id="aaaaaa",
                title="Kizumonogatari I")

            with sqlite3.connect(path) as db:
                numbers = [row[0] for row in db.execute(
                    "SELECT episode_number FROM episodes WHERE related_season_id=? "
                    "ORDER BY episode_number", (season["local_id"],))]
            self.assertEqual([2, 19], numbers)

    def test_incompatible_episode_collision_is_rejected_not_appended(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            series = store.get_or_create_series(
                english_name="Example", root_simkl_id="999", tvdb_id="12345")
            first = self._item("aaaaaa", anilist_id="1", simkl_id="100", media_format="TV")
            second = self._item("bbbbbb", anilist_id="2", simkl_id="200", media_format="TV")
            season = store.add_watchlist_season(
                series["local_id"], first, season_number=1,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=1, last_episode=1, english_name="Season 1")
            store.add_watchlist_season(
                series["local_id"], second, season_number=1,
                provider_path="simkl", placement_source="mapped_tvdb_seasons",
                first_episode=1, last_episode=1, english_name="Season 1")
            store.add_episode(
                season["local_id"], 1, source_episode_number=1,
                simkl_id="episode-a", watchlist_local_id="aaaaaa")

            with self.assertRaises(StructuralCatalogConflict):
                store.add_episode(
                    season["local_id"], 1, source_episode_number=1,
                    simkl_id="episode-b", watchlist_local_id="bbbbbb")
            with sqlite3.connect(path) as db:
                numbers = [row[0] for row in db.execute(
                    "SELECT episode_number FROM episodes WHERE related_season_id=?",
                    (season["local_id"],))]
            self.assertEqual([1], numbers)

    def test_rebuild_requeues_added_watchlist_even_when_catalogue_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            path, store = self._store(root)
            with sqlite3.connect(path) as db:
                db.execute("""UPDATE watchlist_items SET added_to_library=1,
                  mediator_ready=0,mediator_status='RESOLVED'
                  WHERE local_id='aaaaaa'""")
                db.execute("DELETE FROM tv_series")

            self.assertTrue(store.structural_rebuild_required())
            result = store.reset_structural_projection()
            self.assertTrue(result["rebuilt"])
            self.assertFalse(store.structural_rebuild_required())
            self.assertEqual(1, result["watchlist_items"])
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT * FROM watchlist_items WHERE local_id='aaaaaa'"
                ).fetchone()
            self.assertEqual(0, row["added_to_library"])
            self.assertEqual(1, row["mediator_ready"])
            self.assertEqual("PARTIAL", row["mediator_status"])


if __name__ == "__main__":
    unittest.main()
