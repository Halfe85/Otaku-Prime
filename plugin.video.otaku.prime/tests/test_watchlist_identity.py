import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.watchlist_identity import WatchlistIdentityEnrichmentService
from resources.lib.services.watchlist_identity import IdentityMappingConflict,SimklIdentityClient


class WatchlistIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        self.items=WatchlistItemStore(self.path); self.items.initialize()
    def tearDown(self): self.tmp.cleanup()

    def test_one_connected_provider_enriches_all_four_catalog_ids(self):
        self.items.replace_provider_snapshot("mal",[{
          "provider_item_id":"11","ids":{"mal":"11"},"english_name":"Show",
          "list_status":"CURRENT","provider_status":"watching","progress":1}])
        class Client:
            def resolve(self,item):
                self.seen=item
                return {"anilist":"1","mal":"11","kitsu":"21","simkl":"31"}
        client=Client()
        result=WatchlistIdentityEnrichmentService(
          self.items,client=client,request_delay=0).run_once()
        row=self.items.list_all()[0]
        self.assertEqual({"resolved":1,"unresolved":0,"failed":0},result)
        self.assertEqual(("1","11","21","31"),tuple(row[name+"_id"] for name in
          ("anilist","mal","kitsu","simkl")))
        self.assertEqual("11",client.seen["mal_id"])

    def test_resolved_overlap_merges_rows_from_unconnected_trackers(self):
        base={"english_name":"Show","list_status":"CURRENT","provider_status":"watching","progress":1}
        self.items.replace_provider_snapshot("anilist",[dict(base,provider_item_id="1",ids={"anilist":"1"})])
        self.items.replace_provider_snapshot("mal",[dict(base,provider_item_id="11",ids={"mal":"11"})])
        rows={row["anilist_id"]:row for row in self.items.list_all()}
        self.items.apply_resolved_ids(rows["1"]["local_id"],{"anilist":"1","mal":"11","kitsu":"21","simkl":"31"})
        result=self.items.list_all()
        self.assertEqual(1,len(result))
        self.assertEqual("anilist,mal",result[0]["connected_providers"])

    def test_parent_fallback_conflict_is_persisted_and_not_retried(self):
        self.items.replace_provider_snapshot("anilist",[{
          "provider_item_id":"5978","ids":{"anilist":"5978","mal":"5978"},
          "english_name":"Kannagi Special","list_status":"PLANNING",
          "provider_status":"PLANNING","progress":0}])
        class Client:
            def resolve(self,item):
                raise IdentityMappingConflict("Simkl resolved parent AniList 3958")
        result=WatchlistIdentityEnrichmentService(
          self.items,client=Client(),request_delay=0).run_once()
        row=self.items.list_all()[0]
        self.assertEqual(1,result["unresolved"])
        self.assertEqual("CONFLICT",row["identity_resolution_status"])
        self.assertEqual([],self.items.list_missing_provider_ids())

    def test_simkl_client_rejects_detail_for_a_different_anilist_item(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"ids":{"simkl":31,"anilist":3958,"mal":5978}}'
        client=SimklIdentityClient(opener=lambda request,timeout:Response())
        with self.assertRaises(IdentityMappingConflict):
            client.resolve({"anilist_id":"5978","mal_id":"5978","simkl_id":"31"})


if __name__=="__main__": unittest.main()
