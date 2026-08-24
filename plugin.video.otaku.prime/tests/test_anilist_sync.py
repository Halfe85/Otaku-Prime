import os
import sqlite3
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.database.watchlist_preferences import WatchlistPreferenceStore
from resources.lib.users import UserStore
from resources.lib.watchlist.anilist_sync import AniListWatchlistClient, AniListWatchlistImportService
from resources.lib.services.anilist_relations import AniListFranchiseResolverService

class Client:
    def fetch(self,user_id,token):
        return [
          {"status":"CURRENT","progress":2,"media":{"id":1,"isAdult":False,
           "title":{"english":"Show","romaji":"Show"}}},
          {"status":"PLANNING","progress":0,"media":{"id":2,"isAdult":True,
           "title":{"english":"Mature Show","romaji":"Mature"}}},
        ]

class Relations:
    media={
      "1":{"id":1,"title":{"english":"Show","romaji":"Show"},
           "startDate":{"year":2020,"month":1,"day":1},"relations":{"edges":[]}},
      "2":{"id":2,"title":{"english":"Mature Show","romaji":"Mature"},
           "startDate":{"year":2021,"month":1,"day":1},"relations":{"edges":[]}},
    }
    def fetch(self,media_id): return self.media[str(media_id)]

class AniListSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        UserStore(self.path).initialize()
        self.accounts=WatchlistAccountStore(self.path); self.accounts.initialize()
        self.preferences=WatchlistPreferenceStore(self.path); self.preferences.initialize()
        self.media=WatchlistMediaStore(self.path); self.media.initialize()
        self.accounts.save(user_id=1,provider="anilist",external_user_id="7",
          external_username="user",access_token="token")
        self.importer=AniListWatchlistImportService(
          self.accounts,self.preferences,self.media,client=Client())
        self.resolver=AniListFranchiseResolverService(self.media,client=Relations())
    def tearDown(self): self.tmp.cleanup()
    def test_imports_status_and_expands_progress_but_filters_mature(self):
        result=self.importer.sync()
        self.assertEqual({"connected":True,"imported":1,"filtered":1},result)
        self.assertEqual(0,len(self.media.list_media("season")))
        self.resolver.run_once()
        self.assertEqual(2,len(self.media.list_media("episode")))
        with sqlite3.connect(self.path) as db:
            self.assertEqual("CURRENT",db.execute(
              "SELECT list_status FROM provider_list_entries").fetchone()[0])
    def test_mature_switch_includes_adult_entries(self):
        self.preferences.set_mature_content(1,True)
        result=self.importer.sync()
        self.resolver.run_once()
        self.assertEqual(2,result["imported"])
        self.assertEqual(2,len(self.media.list_media("season")))
    def test_switching_mature_off_removes_provider_membership(self):
        self.preferences.set_mature_content(1,True); self.importer.sync(); self.resolver.run_once()
        self.preferences.set_mature_content(1,False); self.importer.sync(); self.resolver.run_once()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(1,db.execute(
              "SELECT COUNT(*) FROM provider_list_entries WHERE provider='anilist'").fetchone()[0])

    def test_relation_discovery_does_not_import_unlisted_sequel(self):
        class ChainRelations:
            media={
              "10":{"id":10,"title":{"english":"Root","romaji":"Root"},
                "startDate":{"year":2020},"relations":{"edges":[
                  {"relationType":"SEQUEL","node":{"id":11,"startDate":{"year":2021}}}]}},
              "11":{"id":11,"title":{"english":"Root 2","romaji":"Root 2"},
                "startDate":{"year":2021},"relations":{"edges":[
                  {"relationType":"PREQUEL","node":{"id":10,"startDate":{"year":2020}}},
                  {"relationType":"SEQUEL","node":{"id":12,"startDate":{"year":2022}}}]}},
              "12":{"id":12,"title":{"english":"Root 3","romaji":"Root 3"},
                "startDate":{"year":2022},"relations":{"edges":[
                  {"relationType":"PREQUEL","node":{"id":11,"startDate":{"year":2021}}}]}},
            }
            def fetch(self,media_id): return self.media[str(media_id)]
        self.media.replace_anilist_staging([{"anilist_id":11,"english_name":"Root 2",
          "romaji_name":"Root 2","list_status":"CURRENT","progress":1}])
        result=AniListFranchiseResolverService(
          self.media,client=ChainRelations()).run_once()
        self.assertEqual(1,result["resolved"])
        seasons=self.media.list_media("season")
        self.assertEqual(["11"],[row["anilist_id"] for row in seasons])
        self.assertEqual(2,seasons[0]["season_number"])

    def test_http_client_identifies_addon_to_anilist(self):
        captured=[]
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"data":{"MediaListCollection":{"lists":[]}}}'
        def opener(request,timeout):
            captured.append(request); return Response()
        AniListWatchlistClient(opener=opener).fetch(7,"token")
        self.assertEqual("Otaku-Prime/0.1.2",captured[0].get_header("User-agent"))
        self.assertEqual("Bearer token",captured[0].get_header("Authorization"))

if __name__=="__main__": unittest.main()
