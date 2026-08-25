import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_items import RAW_COLUMNS, WatchlistItemStore
from resources.lib.users import UserStore
from resources.lib.watchlist.anilist_sync import AniListWatchlistClient, AniListWatchlistImportService


class Client:
    def fetch(self,user_id,token):
        return [
          {"status":"CURRENT","progress":2,"media":{"id":1,"isAdult":False,
           "format":"TV","episodes":12,"startDate":{"year":2020,"month":1,"day":2},
           "title":{"english":"Show","romaji":"Show"}}},
          {"status":"PLANNING","progress":0,"media":{"id":2,"isAdult":True,
           "format":"OVA","title":{"english":"Mature Show","romaji":"Mature"}}},
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

    def test_legacy_processed_columns_are_removed_without_losing_raw_rows(self):
        self.importer.sync()
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE watchlist_items ADD COLUMN franchise_local_id TEXT")
            db.execute("UPDATE watchlist_items SET franchise_local_id='legacy'")
        self.items.initialize()
        with sqlite3.connect(self.path) as db:
            columns={row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
        self.assertEqual(set(RAW_COLUMNS),columns)
        self.assertEqual(2,len(self.items.list_provider("anilist")))

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


if __name__=="__main__": unittest.main()
