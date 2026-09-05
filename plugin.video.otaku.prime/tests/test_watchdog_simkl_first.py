import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0,ROOT)

from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.watchlist_identity_simkl import (
    SimklFirstIdentityClient,
    SimklFirstWatchlistIdentityEnrichmentService,
)


class SimklFirstIdentityClientTests(unittest.TestCase):
    def test_search_id_recovers_when_all_redirects_miss(self):
        class Client(SimklFirstIdentityClient):
            def _redirect_simkl_id(self, provider, value):
                return None
            def _search(self, provider, value):
                if provider == "anilist" and str(value) == "100":
                    return [{"type":"anime","ids":{"simkl":"900"}}]
                return []
            def _detail(self, simkl_id):
                return {"ids":{"simkl":"900","anilist":"100","mal":"200","kitsu":"300"}}

        result=Client().resolve({
            "local_id":"item-a","anilist_id":"100","mal_id":"200","kitsu_id":"300"
        })

        self.assertEqual("900",result["simkl"])
        self.assertEqual("100",result["anilist"])
        self.assertEqual("200",result["mal"])
        self.assertEqual("300",result["kitsu"])

    def test_exact_redirect_does_not_need_title_matching(self):
        class Client(SimklFirstIdentityClient):
            def _simkl_ids(self, ids): return ["901"]
            def _search(self, provider, value): return []
            def _detail(self, simkl_id):
                return {"title":"Completely Different Display Title",
                        "ids":{"simkl":"901","anilist":"101","mal":"201"}}

        result=Client().resolve({
            "local_id":"item-b","anilist_id":"101","mal_id":"201","media_format":"TV"
        })
        self.assertEqual("901",result["simkl"])

    def test_special_reference_requires_exact_episode_tracker_id(self):
        class Client(SimklFirstIdentityClient):
            def _simkl_ids(self, ids): return ["500"]
            def _search(self, provider, value): return []
            def _detail(self, simkl_id):
                return {"ids":{"simkl":"500","anilist":"999","mal":"999"}}
            def _episodes(self, simkl_id):
                return [{
                    "type":"special","title":"Special",
                    "ids":{"anilist":"123","mal":"456"},
                    "tvdb":{"season":0,"episode":8},
                }]

        result=Client().resolve({
            "local_id":"item-c","anilist_id":"123","mal_id":"456",
            "media_format":"SPECIAL"
        })
        self.assertEqual("500",result["_simkl_reference_id"])
        self.assertEqual("S00E08",result["_special_locator"])

    def test_special_reference_rejects_fuzzy_only_episode(self):
        class Client(SimklFirstIdentityClient):
            def _simkl_ids(self, ids): return ["500"]
            def _search(self, provider, value): return []
            def _detail(self, simkl_id):
                return {"ids":{"simkl":"500","anilist":"999","mal":"999"}}
            def _episodes(self, simkl_id):
                return [{
                    "type":"special","title":"Exact Same Title",
                    "ids":{"anilist":"888","mal":"777"},
                    "tvdb":{"season":0,"episode":8},
                }]

        with self.assertRaises(Exception):
            Client().resolve({
                "local_id":"item-d","anilist_id":"123","mal_id":"456",
                "english_name":"Exact Same Title","media_format":"SPECIAL"
            })


class SimklFirstWatchdogGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"users.sqlite")
        self.store=WatchlistItemStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def _insert(self, provider_id="1"):
        self.store.replace_provider_snapshot("anilist",[{
            "provider_item_id":provider_id,
            "ids":{"anilist":provider_id,"mal":"11"},
            "english_name":"Show",
            "media_format":"TV",
            "episode_count":12,
            "release_date":"2025-01-01",
            "list_status":"CURRENT",
            "provider_status":"CURRENT",
            "progress":1,
        }])
        return self.store.list_all()[0]["local_id"]

    def test_missing_simkl_is_not_released_to_mediator(self):
        local_id=self._insert()
        class Client:
            def resolve(self,item): return {"kitsu":"21"}

        SimklFirstWatchlistIdentityEnrichmentService(
            self.store,client=Client(),request_delay=0
        ).run_once()

        row=self.store.item(local_id)
        self.assertIsNone(row["simkl_id"])
        self.assertEqual(0,row["mediator_ready"])

    def test_exact_simkl_is_released_even_if_secondary_id_is_missing(self):
        local_id=self._insert()
        class Client:
            def resolve(self,item): return {"anilist":"1","mal":"11","simkl":"31"}

        SimklFirstWatchlistIdentityEnrichmentService(
            self.store,client=Client(),request_delay=0
        ).run_once()

        row=self.store.item(local_id)
        self.assertEqual("31",row["simkl_id"])
        self.assertEqual(1,row["mediator_ready"])
        self.assertEqual("PARTIAL",row["identity_resolution_status"])


if __name__ == "__main__":
    unittest.main()
