import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore


class SegmentFactory:
    def __init__(self,*values): self.values=iter(values)
    def __call__(self): return next(self.values)


class Alpha10CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        self.watchlist=WatchlistItemStore(self.path); self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist",[{
          "provider_item_id":"1","ids":{"anilist":"1","mal":"11","kitsu":"21","simkl":"31"},
          "english_name":"Season Two","romaji_name":"Season Two","media_format":"TV",
          "list_status":"CURRENT","provider_status":"CURRENT","progress":2}])
        self.item=self.watchlist.list_all()[0]
        self.catalog=CatalogStore(
            self.path,SegmentFactory("a1b2c3","d4e5f6","123456","654321"))
        self.catalog.initialize()
    def tearDown(self): self.tmp.cleanup()

    def test_hierarchical_ids_embed_every_parent(self):
        series=self.catalog.get_or_create_series("Franchise","Franchise",root_simkl_id="30")
        season=self.catalog.add_watchlist_season(series["local_id"],self.item,season_number=2)
        episode=self.catalog.add_episode(
            season["local_id"],1,anilist_id="1",mal_id="11",kitsu_id="21",simkl_id="3101")
        self.assertEqual("a1b2c3",series["local_id"])
        self.assertEqual("a1b2c3d4e5f6",season["local_id"])
        self.assertEqual("a1b2c3d4e5f6123456",episode["local_id"])
        self.assertEqual("a1b2c3",season["related_series_id"])
        self.assertEqual("a1b2c3d4e5f6",episode["related_season_id"])
        self.assertEqual(("1","11","21","3101"),tuple(
            episode[name+"_id"] for name in ("anilist","mal","kitsu","simkl")))

    def test_existing_multi_episode_ova_inherits_watchlist_provider_ids(self):
        series=self.catalog.get_or_create_series("Franchise","Franchise",root_simkl_id="30")
        movie_item=dict(self.item,media_format="OAV")
        season=self.catalog.add_watchlist_season(
            series["local_id"],movie_item,season_number=0)
        first=self.catalog.add_episode(season["local_id"],1)
        self.catalog.add_episode(season["local_id"],2)
        self.assertIsNone(first["anilist_id"])

        self.catalog.initialize()

        repaired=self.catalog.list_episodes(season["local_id"])
        self.assertEqual(2,len(repaired))
        for episode in repaired:
            self.assertEqual(("1","11","21","31"),tuple(
                episode[name+"_id"] for name in ("anilist","mal","kitsu","simkl")))

    def test_only_the_linked_watchlist_item_becomes_a_season(self):
        series=self.catalog.get_or_create_series(root_simkl_id="30")
        season=self.catalog.add_watchlist_season(series["local_id"],self.item,season_number=2)
        self.assertEqual(1,len(self.catalog.list_seasons(series["local_id"])))
        self.assertEqual(self.item["local_id"],season["watchlist_local_id"])
        self.assertEqual(("1","11","21","31"),tuple(season[name+"_id"] for name in
          ("anilist","mal","kitsu","simkl")))

    def test_watchlist_initialization_no_longer_deletes_catalog(self):
        series=self.catalog.get_or_create_series(root_simkl_id="30")
        self.catalog.add_watchlist_season(series["local_id"],self.item,season_number=2)
        WatchlistItemStore(self.path).initialize()
        self.assertEqual(1,len(self.catalog.list_series()))
        self.assertEqual(1,len(self.catalog.list_seasons(series["local_id"])))

    def test_catalog_decodes_entities_on_write_and_repairs_existing_rows(self):
        series=self.catalog.get_or_create_series(
            "An Archdemon&#039;s Dilemma",overview="It&#039;s encoded")
        self.assertEqual("An Archdemon's Dilemma",series["english_name"])
        self.assertEqual("It's encoded",series["overview"])
        with self.catalog._connection() as db:
            db.execute("UPDATE tv_series SET english_name=? WHERE local_id=?",
                       ("A Gatherer&#039;s Adventure",series["local_id"]))
        CatalogStore(self.path).initialize()
        repaired=self.catalog.list_series()[0]
        self.assertEqual("A Gatherer's Adventure",repaired["english_name"])


if __name__=="__main__": unittest.main()
