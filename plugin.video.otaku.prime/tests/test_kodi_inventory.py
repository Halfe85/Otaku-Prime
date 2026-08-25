import json
import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.kodi_inventory import KodiInventoryStore
from resources.lib.connectors.kodi_library import (
  KodiLibraryConnector,KodiLibraryInventoryService,KodiOwnershipReconciler)
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.database.metadata_provider import MetadataProviderStore
from resources.lib.users import UserStore


class KodiInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        UserStore(self.path).initialize()
        self.media=WatchlistMediaStore(self.path); self.media.initialize()
        MetadataProviderStore(self.path).initialize()
        self.store=KodiInventoryStore(self.path); self.store.initialize()
    def tearDown(self): self.tmp.cleanup()

    def test_empty_kodi_library_is_available_not_missing(self):
        class Library:
            def inventory(self): return {"shows":[],"episodes":[],"empty":True}
        result=KodiLibraryInventoryService(Library(),self.store).run_once()
        self.assertEqual(1,result["available"]); self.assertEqual(1,result["empty"])

    def test_connector_reads_complete_inventory(self):
        def execute(payload):
            request=json.loads(payload)
            key="tvshows" if request["method"]=="VideoLibrary.GetTVShows" else "episodes"
            return json.dumps({"result":{key:[]}})
        result=KodiLibraryConnector(execute).inventory()
        self.assertTrue(result["empty"])

    def test_local_episode_wins_provider_identity_match(self):
        franchise=self.media.upsert_tv_series(english_name="Show")
        season=self.media.upsert_season(franchise,1,english_name="Show")
        episode=self.media.upsert_episode(season,1)
        with self.store._connection() as db:
            db.execute("UPDATE tv_series SET metadata_provider='tmdb',metadata_show_id='10' WHERE local_id=?",(franchise,))
            db.execute("UPDATE seasons SET kodi_resolved=1,kodi_season_number=1 WHERE local_id=?",(season,))
            db.execute("UPDATE episodes SET metadata_provider='tmdb',metadata_episode_id='101',kodi_episode_number=1 WHERE local_id=?",(episode,))
        self.store.replace_snapshot(
          [{"tvshowid":7,"title":"Show","file":"/media/Show/","uniqueid":{"tmdb":"10"}}],
          [{"episodeid":8,"tvshowid":7,"title":"One","season":1,"episode":1,
            "file":"/media/Show/S01E01.mkv","uniqueid":{"tmdb":"101"}}])
        result=KodiOwnershipReconciler(self.store).run_once()
        self.assertEqual(1,result["local"])
        link=self.store.list_ownership()[0]
        self.assertEqual("existing_local",link["origin"])
        self.assertEqual("local",link["priority"])

    def test_missing_episode_becomes_pending_prime_projection(self):
        franchise=self.media.upsert_tv_series(english_name="Show")
        season=self.media.upsert_season(franchise,1)
        episode=self.media.upsert_episode(season,1)
        with self.store._connection() as db:
            db.execute("UPDATE tv_series SET metadata_provider='tmdb',metadata_show_id='10' WHERE local_id=?",(franchise,))
            db.execute("UPDATE seasons SET kodi_resolved=1,kodi_season_number=1 WHERE local_id=?",(season,))
            db.execute("UPDATE episodes SET metadata_provider='tmdb',metadata_episode_id='101',kodi_episode_number=1 WHERE local_id=?",(episode,))
        self.store.replace_snapshot([],[])
        self.assertEqual(1,KodiOwnershipReconciler(self.store).run_once()["missing"])
        self.assertEqual("pending",self.store.list_ownership()[0]["ownership"])


if __name__=="__main__": unittest.main()
