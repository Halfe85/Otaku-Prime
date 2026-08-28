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
            def resolve(self,item): self.seen=item; return {"anilist":"1","mal":"11","kitsu":"21","simkl":"31"}
        client=Client(); result=WatchlistIdentityEnrichmentService(self.items,client=client,request_delay=0).run_once()
        row=self.items.list_all()[0]
        self.assertEqual({"complete":1,"partial":0,"unavailable":0,"failed":0},result)
        self.assertEqual(("1","11","21","31"),tuple(row[name+"_id"] for name in ("anilist","mal","kitsu","simkl")))

    def test_resolved_overlap_merges_rows_from_unconnected_trackers(self):
        base={"english_name":"Show","list_status":"CURRENT","provider_status":"watching","progress":1}
        self.items.replace_provider_snapshot("anilist",[dict(base,provider_item_id="1",ids={"anilist":"1"})])
        self.items.replace_provider_snapshot("mal",[dict(base,provider_item_id="11",ids={"mal":"11"})])
        rows={row["anilist_id"]:row for row in self.items.list_all()}
        self.items.apply_resolved_ids(rows["1"]["local_id"],{"anilist":"1","mal":"11","kitsu":"21","simkl":"31"})
        result=self.items.list_all(); self.assertEqual(1,len(result)); self.assertEqual("anilist,mal",result[0]["connected_providers"])

    def test_true_identity_conflict_is_terminal(self):
        self.items.replace_provider_snapshot("anilist",[{
          "provider_item_id":"1","ids":{"anilist":"1","mal":"11"},"english_name":"Normal TV season",
          "media_format":"TV","list_status":"PLANNING","provider_status":"PLANNING","progress":0}])
        class Client:
            def resolve(self,item): raise IdentityMappingConflict("different normal TV identity")
        result=WatchlistIdentityEnrichmentService(self.items,client=Client(),request_delay=0).run_once()
        row=self.items.list_all()[0]
        self.assertEqual(1,result["unavailable"]); self.assertEqual("CONFLICT_EXACT",row["identity_resolution_status"])

    def test_special_parent_redirect_becomes_simkl_reference_and_locator(self):
        self.items.replace_provider_snapshot("anilist",[{
          "provider_item_id":"5978","ids":{"anilist":"5978","mal":"5978"},"english_name":"Kannagi Special",
          "media_format":"SPECIAL","list_status":"PLANNING","provider_status":"PLANNING","progress":0}])
        class Client(SimklIdentityClient):
            def _simkl_ids(self,ids): return ["3958"]
            def _detail(self,simkl_id): return {"ids":{"simkl":"3958","anilist":"3958","mal":"3958"}}
            def _search(self,provider,value): return []
            def _episodes(self,simkl_id):
                return [{"type":"special","title":"Kannagi Special","ids":{"anilist":"5978","mal":"5978"},
                         "tvdb":{"season":0,"episode":8}}]
        result=WatchlistIdentityEnrichmentService(self.items,client=Client(),request_delay=0).run_once()
        row=self.items.list_all()[0]
        self.assertEqual("5978",row["anilist_id"]); self.assertEqual("5978",row["mal_id"])
        self.assertIsNone(row["simkl_id"]); self.assertEqual("3958",row["simkl_reference_id"])
        self.assertEqual("S00E08",row["special_locator"]); self.assertEqual(1,row["mediator_ready"])
        self.assertEqual(1,result["partial"])

    def test_missing_provider_catalog_ids_are_partial_not_failed(self):
        self.items.replace_provider_snapshot("mal",[{
          "provider_item_id":"11","ids":{"mal":"11"},"english_name":"Show",
          "list_status":"CURRENT","provider_status":"watching","progress":1}])
        class Client:
            def resolve(self,item): return {"mal":"11","simkl":"31"}
        result=WatchlistIdentityEnrichmentService(self.items,client=Client(),request_delay=0).run_once()
        self.assertEqual({"complete":0,"partial":1,"unavailable":0,"failed":0},result)
        self.assertEqual("PARTIAL",self.items.list_all()[0]["identity_resolution_status"])

    def test_simkl_client_rejects_detail_for_a_different_normal_anilist_item(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return b'{"ids":{"simkl":31,"anilist":3958,"mal":5978}}'
        client=SimklIdentityClient(opener=lambda request,timeout:Response())
        with self.assertRaises(IdentityMappingConflict):
            client.resolve({"anilist_id":"5978","mal_id":"5978","simkl_id":"31","media_format":"TV"})

    def test_simkl_client_recovers_exact_special_after_parent_redirect(self):
        class Client(SimklIdentityClient):
            def _simkl_ids(self,ids): return ["31"]
            def _detail(self,simkl_id):
                if simkl_id=="31": return {"ids":{"simkl":31,"anilist":3958,"mal":3958}}
                return {"ids":{"simkl":32,"anilist":5978,"mal":5978,"kitsu":99}}
            def _search(self,provider,value): return [{"type":"anime","ids":{"simkl":32}}]
        result=Client().resolve({"anilist_id":"5978","mal_id":"5978","media_format":"SPECIAL"})
        self.assertEqual({"simkl":"32","anilist":"5978","mal":"5978","kitsu":"99"},result)

    def test_watchlist_enrichment_tries_mal_when_anilist_has_no_simkl_mapping(self):
        class Client(SimklIdentityClient):
            def _redirect_simkl_id(self,provider,value): return None if provider=="anilist" else "40634" if provider=="mal" else None
            def _detail(self,simkl_id): return {"ids":{"simkl":"40634","anilist":"8861","mal":"8861"}}
        result=Client().resolve({"anilist_id":"8861","mal_id":"8861"})
        self.assertEqual({"simkl":"40634","anilist":"8861","mal":"8861"},result)

    def test_identity_progress_callback_fires_in_ten_percent_buckets(self):
        entries=[]
        for index in range(10):
            entries.append({"provider_item_id":str(100+index),
              "ids":{"anilist":str(100+index),"mal":str(200+index),"kitsu":str(300+index),"simkl":str(400+index)},
              "english_name":"Show {:02d}".format(index),"list_status":"PLANNING","provider_status":"PLANNING","progress":0})
        self.items.replace_provider_snapshot("anilist",entries)
        calls=[]
        WatchlistIdentityEnrichmentService(self.items,client=object(),request_delay=0,
          on_progress=lambda value:calls.append(value["percent"])).run_once()
        self.assertEqual([10,20,30,40,50,60,70,80,90,100],calls)

    def test_v1_terminal_identity_rows_are_requeued_for_v3(self):
        self.items.replace_provider_snapshot("anilist",[{
          "provider_item_id":"8861","ids":{"anilist":"8861","mal":"8861"},"english_name":"Yosuga no Sora",
          "list_status":"COMPLETED","progress":12}])
        row=self.items.list_all()[0]
        with self.items._connection() as db:
            db.execute("""UPDATE watchlist_items SET identity_resolution_status='NOT_FOUND',
              identity_resolution_version=1 WHERE local_id=?""",(row["local_id"],))
        self.items.initialize(); migrated=self.items.list_all()[0]
        self.assertEqual("PENDING",migrated["identity_resolution_status"]); self.assertEqual(3,migrated["identity_resolution_version"])


if __name__=="__main__": unittest.main()
