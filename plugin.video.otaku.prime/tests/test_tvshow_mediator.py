import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.mediator_tvshow import TVShowMediatorService
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    SimklMediatorHelper,
    _episodes,
)


class SegmentFactory:
    def __init__(self): self.value=0
    def __call__(self): self.value+=1; return "{:06x}".format(self.value)

class Helper:
    def __init__(self,provider,placement): self.provider=provider; self.placement=placement; self.calls=[]
    def resolve(self,item,client): self.calls.append(item["local_id"]); return self.placement
class FailingHelper(Helper):
    def resolve(self,item,client): self.calls.append(item["local_id"]); raise RuntimeError(self.provider+" unavailable")


class PendingProcessor:
    def resolve(self,item):
        raise MediatorMetadataPending("Episode metadata has not been published")


class StructurallyPendingProcessor:
    def resolve(self,item):
        placement={
            "provider_path":"anilist","provider_id":"185874",
            "tv_show":{"name":"Bleach","romaji_name":"Bleach",
                       "anilist_id":"269","source_format":"TV",
                       "air_status":"NOT_YET_RELEASED"},
            "season":{"number":18,"number_source":"anilist_prequel_position",
                      "name":"Bleach Season 18","romaji_name":"Bleach Future",
                      "first_episode":None,"last_episode":None,
                      "release_date":None,"release_status":"NOT_YET_RELEASED"},
            "episodes":[],"relation_path":["269","185874"],
        }
        raise MediatorMetadataPending(
            "Episode metadata has not been published",placement=placement)


class WatchlistTVShowMediatorTests(unittest.TestCase):
    def setUp(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close(); self.path=handle.name
        self.watchlist=WatchlistItemStore(self.path); self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist",[{
            "provider_item_id":"185874","ids":{"anilist":"185874","simkl":"2671730"},
            "english_name":"BLEACH: Thousand-Year Blood War - The Calamity",
            "list_status":"PLANNING","progress":0,"raw":{}}])
        self.watchlist.finalize_merge(); self.prime_id=self.watchlist.list_all()[0]["local_id"]
        self.watchlist.mark_mediator_ready(self.prime_id,True)
        self.catalog=CatalogStore(self.path,SegmentFactory()); self.catalog.initialize()
    def tearDown(self):
        for suffix in ("","-wal","-shm"):
            try: os.unlink(self.path+suffix)
            except FileNotFoundError: pass

    @staticmethod
    def placement(provider="simkl"):
        return {"provider_path":provider,"provider_id":"2671730",
                "tv_show":{"name":"Bleach","simkl_id":"41066","tvdb_id":"74796","source":"simkl_tvdb_anime_group"},
                "season":{"number":17,"number_source":"mapped_tvdb_seasons","name":"The Calamity","first_episode":41,"last_episode":42},
                "episodes":[
                    {"source_episode_number":1,"episode_number":41,"season_number":17,"simkl_id":"9001","mal_id":None,"release_date":"2026-01-01"},
                    {"source_episode_number":2,"episode_number":42,"season_number":17,"simkl_id":"9002","mal_id":None,"release_date":"2026-01-08"}],
                "relation_path":["41066","2671730"]}

    def test_simkl_priority_and_franchise_episode_numbers_are_persisted(self):
        helpers={name:Helper(name,self.placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        service=TVShowMediatorService(self.watchlist,self.catalog,client=object(),helpers=helpers)
        result=service.run_once(); self.assertEqual(1,result["placed"]); self.assertEqual([self.prime_id],helpers["simkl"].calls)
        self.assertEqual([],helpers["anilist"].calls)
        series=self.catalog.list_series()[0]; season=self.catalog.list_seasons(series["local_id"])[0]
        episodes=self.catalog.list_episodes(season["local_id"])
        self.assertEqual((17,41,42,"simkl"),(season["season_number"],season["first_episode"],season["last_episode"],season["provider_path"]))
        self.assertEqual([(1,41),(2,42)],[(row["source_episode_number"],row["episode_number"]) for row in episodes])
        self.assertEqual(1,self.watchlist.item(self.prime_id)["added_to_library"])

    def test_anilist_is_used_when_simkl_is_absent(self):
        item=self.watchlist.list_all()[0]; item["simkl_id"]=None
        helpers={name:Helper(name,self.placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        service=TVShowMediatorService(self.watchlist,self.catalog,client=object(),helpers=helpers)
        self.assertEqual("anilist",service.provider_for(item))

    def test_anilist_is_tried_after_present_simkl_id_fails(self):
        helpers={name:Helper(name,self.placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        helpers["simkl"]=FailingHelper("simkl",None)
        service=TVShowMediatorService(self.watchlist,self.catalog,client=object(),helpers=helpers)
        item=self.watchlist.list_all()[0]; placement=service.resolve_item(item)
        self.assertEqual("anilist",placement["provider_path"]); self.assertEqual([self.prime_id],helpers["simkl"].calls)
        self.assertEqual([self.prime_id],helpers["anilist"].calls); self.assertEqual("simkl",placement["provider_attempts"][0]["provider"])

    def test_special_rows_are_selected_only_for_a_special_watchlist_item(self):
        rows=[{"type":"episode","episode":1,"ids":{"simkl_id":1},"tvdb":{"season":2,"episode":1}},
              {"type":"special","episode":1,"ids":{"simkl_id":2},"tvdb":{"season":0,"episode":8}}]
        self.assertEqual(["1"],[row["simkl_id"] for row in _episodes(rows,False)])
        self.assertEqual(["1","2"],[row["simkl_id"] for row in _episodes(rows,True)])

    def test_simkl_mediator_uses_only_the_canonical_simkl_id(self):
        class Client:
            def exact_simkl_id(self,*args): raise AssertionError("mediator must not search Simkl with a foreign ID")
        self.assertEqual("2671730",SimklMediatorHelper().resolve_simkl_id({"simkl_id":"2671730","anilist_id":"185874"},Client()))

    def test_unreleased_item_is_deferred_without_catalog_rows_or_error(self):
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=PendingProcessor())
        result=service.run_once()
        item=self.watchlist.item(self.prime_id)
        self.assertEqual({"placed":0,"existing":0,"deferred":1,"failed":0},result)
        self.assertEqual("DEFERRED",item["mediator_status"])
        self.assertEqual(0,item["mediator_ready"])
        self.assertEqual(0,item["added_to_library"])
        self.assertEqual([],self.catalog.list_series())

    def test_unchanged_deferred_metadata_does_not_repeat_the_item_notice(self):
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=PendingProcessor())
        service.run_once()
        self.watchlist.mark_mediator_ready(self.prime_id,True)
        with self.assertLogs("otaku_prime.services-mediator_tvshow",level="INFO") as logs:
            result=service.run_once()
        self.assertEqual(1,result["deferred"])
        self.assertFalse(any(
            "Mediator deferred Prime item" in message for message in logs.output))

    def test_unreleased_item_keeps_structural_library_position_and_watchlist_link(self):
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),
            processor=StructurallyPendingProcessor())

        result=service.run_once()

        item=self.watchlist.item(self.prime_id)
        series=self.catalog.list_series()[0]
        season=self.catalog.list_seasons(series["local_id"])[0]
        self.assertEqual({"placed":0,"existing":0,"deferred":1,"failed":0},result)
        self.assertEqual("DEFERRED",item["mediator_status"])
        self.assertEqual(0,item["added_to_library"])
        self.assertEqual(self.prime_id,season["watchlist_local_id"])
        self.assertEqual(18,season["season_number"])
        self.assertEqual("Bleach Season 18",season["english_name"])
        self.assertEqual("NOT_YET_RELEASED",season["release_status"])
        self.assertEqual("STRUCTURE_ONLY",season["placement_state"])
        self.assertEqual([],self.catalog.list_episodes(season["local_id"]))

        # Reinitialization must not mistake a structure-only placeholder for
        # a completely published library season.
        self.watchlist.initialize()
        self.assertEqual(0,self.watchlist.item(self.prime_id)["added_to_library"])


if __name__=="__main__": unittest.main()
