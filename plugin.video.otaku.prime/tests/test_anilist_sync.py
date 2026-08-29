import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.users import UserStore
from resources.lib.watchlist.anilist_sync import AniListWatchlistClient, AniListWatchlistImportService


class Client:
    def fetch(self,user_id,token):
        return [
          {"status":"CURRENT","progress":2,"updatedAt":100,"media":{"id":1,"idMal":11,"isAdult":False,
           "format":"TV","episodes":12,"startDate":{"year":2020,"month":1,"day":2},
           "synonyms":["Show Alias","Show &amp; Friends"],
           "title":{"english":"Show","romaji":"Show","userPreferred":"Preferred Show"}}},
          {"status":"PLANNING","progress":0,"media":{"id":2,"isAdult":True,
           "format":"OVA","synonyms":["Mature Alias"],
           "title":{"english":None,"romaji":"Mature","userPreferred":"Mature Preferred"}}},
        ]


class AniListSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        UserStore(self.path).initialize()
        self.accounts=WatchlistAccountStore(self.path); self.accounts.initialize()
        self.items=WatchlistItemStore(self.path); self.items.initialize()
        self.accounts.save(user_id=1,provider="anilist",external_user_id="7",
          external_username="user",access_token="token")
        self.importer=AniListWatchlistImportService(self.accounts,self.items,client=Client())

    def tearDown(self): self.tmp.cleanup()

    def test_imports_complete_raw_snapshot_including_adult_items(self):
        result=self.importer.sync(); rows=self.items.list_provider("anilist")
        self.assertEqual({"connected":True,"imported":2,"filtered":0,"watchlist_rows":2},result)
        rows_by_id={row["provider_item_id"]:row for row in rows}
        self.assertEqual("CURRENT",rows_by_id["1"]["list_status"])
        self.assertEqual("PLANNING",rows_by_id["2"]["list_status"])
        self.assertEqual("2020-01-02",rows_by_id["1"]["release_date"])
        self.assertEqual(1,rows_by_id["2"]["is_adult"])
        self.assertEqual(2,json.loads(rows_by_id["2"]["raw_json"])["media"]["id"])

    def test_preferred_and_alternative_titles_are_stored_for_ui_and_search(self):
        self.importer.sync()
        self.items.set_mature(1)
        rows={row["anilist_id"]:row for row in self.items.list_ui_items()}
        self.assertEqual("Preferred Show",rows["1"]["preferred_name"])
        self.assertEqual(["Show Alias","Show & Friends"],rows["1"]["alternative_titles"])
        self.assertIsNone(rows["2"]["english_name"])
        self.assertEqual("Mature Preferred",rows["2"]["preferred_name"])
        self.assertEqual(["Mature Alias"],rows["2"]["alternative_titles"])

    def test_duplicate_provider_item_is_stored_once(self):
        class DuplicateClient:
            def fetch(self,user_id,token):
                entry={"status":"CURRENT","progress":2,"media":{"id":1,
                  "isAdult":False,"title":{"english":"Show","romaji":"Show"}}}
                return [entry,dict(entry)]
        result=AniListWatchlistImportService(
          self.accounts,self.items,client=DuplicateClient()).sync()
        self.assertEqual(1,result["imported"])
        self.assertEqual(1,len(self.items.list_provider("anilist")))

    def test_disconnected_provider_removes_its_snapshot(self):
        self.importer.sync(); self.accounts.delete(1,"anilist")
        self.assertFalse(self.importer.sync()["connected"])
        self.assertEqual([],self.items.list_provider("anilist"))

    def test_native_cross_ids_are_saved_on_canonical_item(self):
        self.importer.sync(); self.items.finalize_merge()
        row={item["anilist_id"]:item for item in self.items.list_all()}["1"]
        self.assertEqual("11",row["mal_id"])
        self.assertEqual("CURRENT",row["status"])

    def test_repeating_is_preserved_but_normalized_to_watching(self):
        class RepeatingClient:
            def fetch(self,user_id,token):
                return [{"status":"REPEATING","progress":3,"media":{"id":9,
                  "title":{"english":"Again"}}}]
        AniListWatchlistImportService(self.accounts,self.items,client=RepeatingClient()).sync()
        row=self.items.list_provider("anilist")[0]
        self.assertEqual("REPEATING",row["provider_status"])
        self.assertEqual("CURRENT",row["status"])

    def test_http_client_identifies_addon_to_anilist(self):
        captured=[]
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"data":{"MediaListCollection":{"lists":[]}}}'
        def opener(request,timeout): captured.append(request); return Response()
        AniListWatchlistClient(opener=opener).fetch(7,"token")
        self.assertEqual("Otaku-Prime/0.1.2",captured[0].get_header("User-agent"))
        self.assertEqual("Bearer token",captured[0].get_header("Authorization"))
        query=json.loads(captured[0].data.decode("utf-8"))["query"]
        self.assertIn("synonyms",query)
        self.assertIn("userPreferred",query)


if __name__=="__main__": unittest.main()
