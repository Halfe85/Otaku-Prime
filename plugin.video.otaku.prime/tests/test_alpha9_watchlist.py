import os
import sys
import tempfile
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.database.watchlist_items import WatchlistIdentityConflict,WatchlistItemStore


def entry(item_id,status,progress,ids,title="Show"):
    return {"provider_item_id":str(item_id),"ids":ids,"english_name":title,
      "list_status":status,"provider_status":status,"progress":progress,"raw":{"id":item_id}}


class Alpha9CanonicalWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,"db.sqlite")
        self.store=WatchlistItemStore(self.path); self.store.initialize()
    def tearDown(self): self.tmp.cleanup()

    def test_four_provider_snapshots_merge_into_one_watchlist_item(self):
        self.store.replace_provider_snapshot("anilist",[entry(1,"CURRENT",3,{"anilist":1,"mal":11})])
        self.store.replace_provider_snapshot("mal",[entry(11,"CURRENT",3,{"mal":11})])
        self.store.replace_provider_snapshot("kitsu",[entry(21,"CURRENT",3,{"kitsu":21,"mal":11})])
        self.store.replace_provider_snapshot("simkl",[entry(31,"CURRENT",3,{"simkl":31,"anilist":1,"mal":11,"kitsu":21})])
        result=self.store.finalize_merge(); rows=self.store.list_all()
        self.assertEqual(1,result["items"]); self.assertEqual(1,len(rows))
        self.assertEqual(("1","11","21","31"),tuple(rows[0][key] for key in
          ("anilist_id","mal_id","kitsu_id","simkl_id")))
        self.assertEqual({"anilist","mal","kitsu","simkl"},set(rows[0]["connected_providers"].split(",")))

    def test_prime_master_state_is_not_overwritten_by_later_fetch(self):
        self.store.replace_provider_snapshot("anilist",[entry(1,"CURRENT",3,{"anilist":1})])
        self.store.finalize_merge(); local_id=self.store.list_all()[0]["local_id"]
        self.store.set_master_state(local_id,"PAUSED",4)
        self.store.replace_provider_snapshot("anilist",[entry(1,"CURRENT",5,{"anilist":1})])
        self.store.finalize_merge(); row=self.store.list_all()[0]
        self.assertEqual(("PAUSED",4),(row["status"],row["progress"]))
        self.assertEqual(1,row["has_conflict"])

    def test_provider_removal_keeps_item_while_another_provider_owns_it(self):
        self.store.replace_provider_snapshot("anilist",[entry(1,"CURRENT",1,{"anilist":1,"mal":11})])
        self.store.replace_provider_snapshot("mal",[entry(11,"CURRENT",1,{"mal":11})])
        self.store.replace_provider_snapshot("anilist",[])
        self.assertEqual(1,len(self.store.list_all()))
        self.store.replace_provider_snapshot("mal",[])
        self.assertEqual([],self.store.list_all())

    def test_conflicting_verified_ids_are_not_silently_merged(self):
        self.store.replace_provider_snapshot("anilist",[entry(1,"CURRENT",1,{"anilist":1,"mal":11})])
        self.store.replace_provider_snapshot("kitsu",[entry(21,"CURRENT",1,{"kitsu":21,"mal":22})])
        with self.assertRaises(WatchlistIdentityConflict):
            self.store.replace_provider_snapshot("simkl",[entry(31,"CURRENT",1,
              {"simkl":31,"anilist":1,"kitsu":21})])
        self.assertEqual(2,len(self.store.list_all()))


if __name__=="__main__": unittest.main()
