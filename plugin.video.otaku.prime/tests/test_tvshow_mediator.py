import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.mediator_tvshow import TVShowMediatorService
from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint
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


class HelperProcessor:
    def __init__(self,placement): self.placement=placement
    def resolve(self,item): return self.placement


class NoFanart:
    def enrich(self,placement): return placement


class PosterEndpoint:
    def __init__(self,provider,url): self.provider=provider; self.url=url; self.calls=[]
    def poster(self,provider_id): self.calls.append(str(provider_id)); return self.url


class PosterFallbackProcessor(HelperProcessor):
    def __init__(self,placement,endpoints):
        super().__init__(placement); self.endpoints=endpoints


class ClassificationEndpoint:
    def __init__(self,value): self.value=value; self.calls=[]
    def classification(self,provider_id):
        self.calls.append(str(provider_id)); return self.value


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


class VanishingWatchlistProcessor:
    def __init__(self,watchlist,placement):
        self.watchlist=watchlist; self.placement=placement
    def resolve(self,item):
        self.watchlist.replace_provider_snapshot("anilist",[])
        return self.placement


class AniListCastClient:
    def __init__(self): self.calls=[]
    def cast(self,anilist_id):
        self.calls.append(str(anilist_id))
        return [{"person":{"anilist_id":"700","name":"Voice Actor",
                           "image_url":"https://img/staff.jpg"},
                 "character":{"anilist_id":"800","name":"Hero",
                              "image_url":"https://img/hero.jpg"}}]


class SimklPlacementWithAniListCredits:
    def __init__(self,placement):
        self.placement=placement
        endpoint=type("AniListEndpoint",(),{})()
        endpoint.client=AniListCastClient()
        self.endpoints={"anilist":endpoint}
    def resolve(self,item): return self.placement


class WatchlistTVShowMediatorTests(unittest.TestCase):
    def setUp(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close(); self.path=handle.name
        self.watchlist=WatchlistItemStore(self.path); self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist",[{
            "provider_item_id":"185874","ids":{"anilist":"185874","mal":"62401",
                                                   "kitsu":"50001","simkl":"2671730"},
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

    def test_special_provider_ids_follow_into_every_ova_episode(self):
        placement=self.placement("anilist")
        placement["library_type"]="series"
        placement["season"].update({
            "number":0,"media_type":"ova","name":"Bleach OVA",
            "first_episode":5,"last_episode":6})
        placement["episodes"]=[
            {"source_episode_number":1,"episode_number":5,
             "season_number":0,"simkl_id":"remote-episode-1","mal_id":None,
             "release_date":"2008-12-13"},
            {"source_episode_number":2,"episode_number":6,
             "season_number":0,"simkl_id":"remote-episode-2","mal_id":None,
             "release_date":"2009-01-10"}]
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),
            processor=HelperProcessor(placement),fanart=NoFanart())

        result=service.run_once()

        season=self.catalog.list_seasons(self.catalog.list_series()[0]["local_id"])[0]
        episodes=self.catalog.list_episodes(season["local_id"])
        self.assertEqual(1,result["placed"])
        self.assertEqual(2,len(episodes))
        for episode in episodes:
            self.assertEqual(("185874","62401","50001","2671730"),tuple(
                episode[name+"_id"] for name in ("anilist","mal","kitsu","simkl")))

    def test_mediator_persists_series_artwork(self):
        placement=self.placement("simkl")
        placement["tv_show"].update({
            "poster_url":"https://img.example/poster.webp",
            "clearlogo_url":"https://img.example/logo.webp",
            "banner_url":"https://img.example/banner.webp",
        })
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=HelperProcessor(placement))

        result=service.run_once()
        series=self.catalog.list_series()[0]

        self.assertEqual(1,result["placed"])
        self.assertEqual("https://img.example/poster.webp",series["poster_url"])
        self.assertEqual("https://img.example/logo.webp",series["clearlogo_url"])
        self.assertEqual("https://img.example/banner.webp",series["banner_url"])

    def test_missing_fanart_poster_uses_provider_priority_fallback(self):
        placement=self.placement("simkl")
        placement["tv_show"].update({
            "anilist_id":"269","mal_id":"269","kitsu_id":"12","simkl_id":"41066"})
        endpoints={
            "anilist":PosterEndpoint("anilist",None),
            "mal":PosterEndpoint("mal",None),
            "kitsu":PosterEndpoint("kitsu","https://img.example/kitsu-poster.webp"),
            "simkl":PosterEndpoint("simkl","https://img.example/simkl-poster.webp"),
        }
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),
            processor=PosterFallbackProcessor(placement,endpoints),fanart=NoFanart())

        result=service.run_once()
        series=self.catalog.list_series()[0]

        self.assertEqual(1,result["placed"])
        self.assertEqual("https://img.example/kitsu-poster.webp",series["poster_url"])
        self.assertEqual(["269"],endpoints["anilist"].calls)
        self.assertEqual(["269"],endpoints["mal"].calls)
        self.assertEqual(["12"],endpoints["kitsu"].calls)
        self.assertEqual([],endpoints["simkl"].calls)

    def test_simkl_structure_is_enriched_with_anilist_classification(self):
        placement=self.placement("simkl")
        placement["tv_show"].update({
            "anilist_id":"269","genres":["Action"],"themes":[],
            "age_rating":None,"mature":False})
        endpoints={
            "anilist":ClassificationEndpoint({
                "genres":["Action","Drama"],"themes":["Military"],
                "age_rating":"18+","mature":True}),
            "mal":ClassificationEndpoint({}),
            "kitsu":ClassificationEndpoint({}),
            "simkl":ClassificationEndpoint({}),
        }
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),
            processor=PosterFallbackProcessor(placement,endpoints),fanart=NoFanart())

        result=service.run_once()
        detail=self.catalog.library_series_detail(self.catalog.list_series()[0]["local_id"])

        self.assertEqual(1,result["placed"])
        self.assertEqual(["Action","Drama"],detail["genres"])
        self.assertEqual(["Military"],detail["themes"])
        self.assertEqual("18+",detail["age_rating"])
        self.assertEqual(1,detail["mature"])
        self.assertEqual(["269"],endpoints["anilist"].calls)
        self.assertEqual([],endpoints["mal"].calls)

    def test_anilist_is_used_when_simkl_is_absent(self):
        item=self.watchlist.list_all()[0]; item["simkl_id"]=None
        helpers={name:Helper(name,self.placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        service=TVShowMediatorService(self.watchlist,self.catalog,client=object(),helpers=helpers)
        self.assertEqual("anilist",service.provider_for(item))

    def test_simkl_placement_fetches_staff_and_characters_from_anilist_ids(self):
        placement=self.placement("simkl")
        placement["tv_show"]["anilist_id"]="269"
        processor=SimklPlacementWithAniListCredits(placement)
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=processor)

        result=service.run_once()

        self.assertEqual(1,result["placed"])
        detail=self.catalog.library_series_detail(self.catalog.list_series()[0]["local_id"])
        self.assertEqual([],detail["staff"])
        self.assertEqual("Hero",detail["characters"][0]["name"])
        self.assertEqual("Voice Actor",detail["characters"][0]["staff"][0]["name"])
        self.assertEqual("https://img/staff.jpg",detail["cast"][0]["person"]["image_url"])
        self.assertEqual(["269","185874"],processor.endpoints["anilist"].client.calls)

    def test_same_anilist_root_and_season_fetch_enrichment_only_once(self):
        placement=self.placement("simkl")
        placement["tv_show"]["anilist_id"]="185874"
        processor=SimklPlacementWithAniListCredits(placement)
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=processor)

        service.run_once()

        self.assertEqual(["185874"],processor.endpoints["anilist"].client.calls)

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

    def test_simkl_special_range_resolves_every_requested_s00_episode(self):
        class Client:
            def anime(self,_simkl_id):
                return {"ids":{"simkl":"900","tvdb":"100"},"title":"Parent",
                        "anime_type":"tv","relations":[]}
            def tv_franchise(self,_target,root_detail=None):
                return {"name":"Parent","simkl_id":"900","tvdb_id":"100",
                        "source":"simkl_tvdb_anime_group"}
            def episodes(self,_simkl_id):
                return [
                    {"type":"special","episode":1,"ids":{"simkl_id":"8001"},
                     "tvdb":{"season":0,"episode":8}},
                    {"type":"special","episode":2,"ids":{"simkl_id":"8002"},
                     "tvdb":{"season":0,"episode":9}}]

        placement=SimklMediatorEndpoint(Client()).resolve({
            "simkl_id":None,"simkl_reference_id":"900",
            "special_locator":"S00E08-E09","media_format":"OAV",
            "english_name":"Example OAV"})

        self.assertEqual((0,8,9),(
            placement["season"]["number"],placement["season"]["first_episode"],
            placement["season"]["last_episode"]))
        self.assertEqual([8,9],[row["episode_number"] for row in placement["episodes"]])

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

    def test_vanished_watchlist_row_cancels_stale_worker_without_catalog_writes(self):
        processor=VanishingWatchlistProcessor(self.watchlist,self.placement())
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=processor)

        with self.assertLogs("otaku_prime.services-mediator_tvshow",level="INFO") as logs:
            result=service.run_once()

        self.assertEqual({"placed":0,"existing":0,"deferred":0,"failed":0},result)
        self.assertEqual([],self.catalog.list_series())
        self.assertTrue(any("discarded stale Prime item" in message for message in logs.output))

    def test_stopped_mediator_cannot_be_restarted_by_late_watchdog_callback(self):
        service=TVShowMediatorService(
            self.watchlist,self.catalog,client=object(),processor=PendingProcessor())

        self.assertTrue(service.stop(timeout=0))
        self.assertEqual(
            {"scheduled":False,"busy":False,"stopping":True},service.start())


if __name__=="__main__": unittest.main()
