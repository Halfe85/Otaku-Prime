import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.users import UserStore
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.watchlist.provider_importers import (
  MALWatchlistClient,MALWatchlistImportService,KitsuWatchlistImportService,
  SimklWatchlistImportService)


class ProviderImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        UserStore(self.path).initialize(); self.accounts=WatchlistAccountStore(self.path); self.accounts.initialize()
        self.items=WatchlistItemStore(self.path); self.items.initialize()
    def tearDown(self): self.tmp.cleanup()
    def connect(self,provider):
        self.accounts.save(user_id=1,provider=provider,external_user_id="7",
          external_username="user",access_token="token",refresh_token="refresh",token_expires_at=9999999999)

    def test_http_client_logs_sanitized_endpoint_without_query_or_token(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"data":[],"paging":{}}'
        client=MALWatchlistClient(opener=lambda request,timeout: Response())
        with self.assertLogs("otaku_prime.watchlist-provider_importers",level="INFO") as captured:
            client.fetch("secret-token")
        output="\n".join(captured.output)
        self.assertIn("GET https://api.myanimelist.net/v2/users/@me/animelist",output)
        self.assertNotIn("fields=",output)
        self.assertNotIn("secret-token",output)

    def test_mal_fetch_normalizes_status_progress_and_native_id(self):
        self.connect("mal")
        class Client:
            def fetch(self,token): return [{"node":{"id":11,"title":"Show","alternative_titles":{"en":"Show"},
              "num_episodes":12,"media_type":"tv"},"list_status":{"status":"on_hold","num_episodes_watched":4}}]
        MALWatchlistImportService(self.accounts,self.items,client=Client()).sync()
        row=self.items.list_provider("mal")[0]
        self.assertEqual(("PAUSED",4,"11"),(row["status"],row["progress"],row["mal_id"]))

    def test_kitsu_fetch_uses_anilist_and_mal_mappings(self):
        self.connect("kitsu")
        class Client:
            def fetch(self,user_id,token):
                entry={"attributes":{"status":"planned","progress":0},"relationships":{"anime":{"data":{"id":"21"}}}}
                anime={"21":{"id":"21","attributes":{"canonicalTitle":"Show","episodeCount":12,"subtype":"TV"},
                  "relationships":{"mappings":{"data":[{"id":"a"},{"id":"m"}]}}}}
                mappings={"a":{"attributes":{"externalSite":"ANILIST_ANIME","externalId":"1"}},
                  "m":{"attributes":{"externalSite":"MYANIMELIST_ANIME","externalId":"11"}}}
                return [entry],anime,mappings
        KitsuWatchlistImportService(self.accounts,self.items,client=Client()).sync()
        row=self.items.list_provider("kitsu")[0]
        self.assertEqual(("1","11","21"),(row["anilist_id"],row["mal_id"],row["kitsu_id"]))
        self.assertEqual("PLANNING",row["status"])

    def test_simkl_initial_fetch_keeps_all_returned_tracker_ids(self):
        self.connect("simkl")
        class Client:
            def activities(self,token): return {"anime":{"all":"2026-01-01","removed_from_list":""}}
            def anime(self,token,date_from=None,ids_only=False): return [{"status":"watching","watched_episodes_count":5,
              "total_episodes_count":12,"anime_type":"tv","show":{"title":"Show","ids":{
              "simkl":31,"anilist":1,"mal":11,"kitsu":21}}}]
        result=SimklWatchlistImportService(self.accounts,self.items,client=Client()).sync()
        row=self.items.list_provider("simkl")[0]
        self.assertEqual("initial",result["mode"])
        self.assertEqual(("1","11","21","31"),tuple(row[key] for key in
          ("anilist_id","mal_id","kitsu_id","simkl_id")))

    def test_simkl_current_anime_response_object_is_supported(self):
        rows=SimklWatchlistImportService._normalize([{"status":"watching",
          "anime":{"title":"Show","ids":{"simkl":31,"anilist":1,"mal":11,"kitsu":21}}}])
        self.assertEqual("31",rows[0]["provider_item_id"])
        self.assertEqual(1,rows[0]["ids"]["anilist"])

    def test_simkl_titles_are_html_entity_decoded(self):
        rows=SimklWatchlistImportService._normalize([{"status":"watching",
          "anime":{"title":"A Gatherer&#039;s Adventure","ids":{"simkl":31}}}])
        self.assertEqual("A Gatherer's Adventure",rows[0]["english_name"])


if __name__=="__main__": unittest.main()
