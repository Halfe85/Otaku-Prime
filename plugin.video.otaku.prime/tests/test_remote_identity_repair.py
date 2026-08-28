import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.mediator_helper_simkl import SimklMediatorClient
from resources.lib.services.remote_identity import (
    RemoteIdentityAmbiguous,
    best_title_similarity,
    clean_remote_text,
    choose_candidate,
    normalize_title,
    persist_watchlist_id_repair,
)


class SegmentFactory:
    def __init__(self): self.value=0
    def __call__(self):
        self.value+=1
        return "{:06x}".format(self.value)


def prime_item():
    return {
        "local_id":"prime-local",
        "anilist_id":"101506",
        "mal_id":None,
        "kitsu_id":None,
        "simkl_id":"10",
        "english_name":"UzaMaid!",
        "romaji_name":"Uchi no Maid ga Uzasugiru!",
        "native_name":None,
        "release_date":"2018-10-05",
        "episode_count":12,
        "media_format":"TV",
    }


def good_detail(simkl_id="20"):
    return {
        "title":"Uchi no Maid ga Uzasugiru!",
        "en_title":"UzaMaid!",
        "year":2018,
        "anime_type":"tv",
        "total_episodes":12,
        "ids":{"simkl":simkl_id,"anilist":"101506"},
    }


class RemoteIdentityRepairTests(unittest.TestCase):
    def test_remote_entities_are_decoded_and_non_latin_titles_are_preserved(self):
        self.assertEqual("A Gatherer's Adventure",clean_remote_text(
            "A Gatherer&#039;s Adventure"))
        japanese="不死身な僕の日常 シーズン4"
        self.assertNotEqual("4",normalize_title(japanese))
        self.assertLess(best_title_similarity([japanese],["4"]),0.82)

    def test_lookup_prefers_prime_identity_over_stored_remote_id(self):
        bad={
            "title":"Completely Different Show","year":2024,"anime_type":"tv",
            "total_episodes":24,"ids":{"simkl":"10","anilist":"999999"},
        }
        chosen,score=choose_candidate(
            prime_item(),[bad,good_detail()],ignore_provider="simkl")
        self.assertEqual("20",str(chosen["ids"]["simkl"]))
        self.assertGreater(score["score"],100)

    def test_ambiguous_title_lookup_is_not_silently_accepted(self):
        item=prime_item(); item["anilist_id"]=None
        first=good_detail("20"); first["ids"].pop("anilist")
        second=good_detail("21"); second["ids"].pop("anilist")
        with self.assertRaises(RemoteIdentityAmbiguous):
            choose_candidate(item,[first,second],ignore_provider="simkl")

    def test_repair_changes_remote_id_but_preserves_prime_local_id(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close()
        try:
            store=WatchlistItemStore(handle.name); store.initialize()
            store.replace_provider_snapshot("anilist",[{
                "provider_item_id":"101506",
                "ids":{"anilist":"101506","simkl":"10"},
                "english_name":"UzaMaid!","romaji_name":"Uchi no Maid ga Uzasugiru!",
                "list_status":"CURRENT","provider_status":"CURRENT","progress":1,
                "release_date":"2018-10-05","episode_count":12,"media_format":"TV",
            }])
            store.finalize_merge(); before=store.list_all()[0]
            self.assertTrue(persist_watchlist_id_repair(
                store,before["local_id"],"simkl","10","20","stale mapping"))
            after=store.list_all()[0]
            self.assertEqual(before["local_id"],after["local_id"])
            self.assertEqual("20",after["simkl_id"])
            self.assertEqual("REPAIRED",after["identity_resolution_status"])
        finally:
            for suffix in ("","-wal","-shm"):
                try: os.unlink(handle.name+suffix)
                except FileNotFoundError: pass

    def test_simkl_client_repairs_wrong_name_using_exact_foreign_id(self):
        class Client(SimklMediatorClient):
            def anime(self,simkl_id):
                if str(simkl_id)=="10":
                    return {"title":"Wrong Show","year":2020,"anime_type":"tv",
                            "ids":{"simkl":"10","anilist":"999"}}
                return good_detail("20")
            def search_id(self,provider,value):
                return [{"type":"anime","ids":{"simkl":"20"}}] if provider=="anilist" else []
            def search_anime(self,query,limit=20): return []
        detail,repair,score=Client().resolve_anime_identity(prime_item(),"10")
        self.assertEqual("20",str(detail["ids"]["simkl"]))
        self.assertEqual({"provider":"simkl","old":"10","new":"20",
                          "reason":"Stored Simkl ID failed Prime identity validation"},repair)
        self.assertGreater(score["score"],100)

    def test_tvdb_crossmap_with_wrong_name_falls_back_to_title_lookup(self):
        class Client(SimklMediatorClient):
            def search_id(self,provider,value):
                if provider=="tvdb":
                    return [{"type":"tv","title":"Wrong Show","ids":{"simkl":"100","tvdb":"111"}}]
                return []
            def tv(self,simkl_id):
                if str(simkl_id)=="100":
                    return {"title":"Wrong Show","ids":{"simkl":"100","tvdb":"111"}}
                return {"title":"UzaMaid!","ids":{"simkl":"200","tvdb":"352839","tmdb":"82864"}}
            def _get(self,path,params=None):
                if path=="/search/tv":
                    return [{"type":"tv","title":"UzaMaid!",
                             "ids":{"simkl":"200","tvdb":"352839","tmdb":"82864"}}]
                raise AssertionError(path)
        anime={"title":"UzaMaid!","en_title":"UzaMaid!","year":2018,
               "ids":{"simkl":"20","tvdb":"111","tmdb":"82864"}}
        result=Client().tv_franchise(anime,root_detail=anime)
        self.assertEqual("352839",result["tvdb_id"])
        self.assertEqual("simkl_franchise_lookup_repaired",result["source"])

    def test_tvdb_crossmap_accepts_an_ona_franchise(self):
        class Client(SimklMediatorClient):
            def search_id(self,provider,value):
                return [{"type":"anime","anime_type":"ona","title":"Gatherer",
                         "ids":{"simkl":"300","tvdb":"376751"}}]
            def anime(self,simkl_id):
                return {"title":"Gatherer","en_title":"A Gatherer&#039;s Adventure",
                        "anime_type":"ona","ids":{"simkl":"300","tvdb":"376751"}}
        target={"title":"Gatherer","en_title":"A Gatherer&#039;s Adventure",
                "anime_type":"ona","ids":{"simkl":"301","tvdb":"376751"}}
        result=Client().tv_franchise(target,root_detail=target)
        self.assertEqual("A Gatherer's Adventure",result["name"])
        self.assertEqual("300",result["simkl_id"])
        self.assertEqual("simkl_tvdb_anime_group_validated",result["source"])

    def test_catalog_remote_id_change_keeps_series_local_id(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close()
        try:
            store=WatchlistItemStore(handle.name); store.initialize()
            catalog=CatalogStore(handle.name,SegmentFactory()); catalog.initialize()
            first=catalog.get_or_create_series(
                english_name="UzaMaid!",root_simkl_id="10",tvdb_id="111")
            second=catalog.get_or_create_series(
                english_name="UzaMaid!",root_simkl_id="20",tvdb_id="352839")
            self.assertEqual(first["local_id"],second["local_id"])
            self.assertEqual("20",second["root_simkl_id"])
            self.assertEqual("352839",second["tvdb_id"])
        finally:
            for suffix in ("","-wal","-shm"):
                try: os.unlink(handle.name+suffix)
                except FileNotFoundError: pass

    def test_season_and_episode_remote_ids_refresh_without_changing_local_ids(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close()
        try:
            store=WatchlistItemStore(handle.name); store.initialize()
            store.replace_provider_snapshot("anilist",[{
                "provider_item_id":"101506","ids":{"anilist":"101506","simkl":"10"},
                "english_name":"UzaMaid!","list_status":"CURRENT","progress":1,
            }])
            item=store.list_all()[0]
            catalog=CatalogStore(handle.name,SegmentFactory()); catalog.initialize()
            series=catalog.get_or_create_series("UzaMaid!",root_simkl_id="1")
            season=catalog.add_watchlist_season(series["local_id"],item,season_number=1)
            episode=catalog.add_episode(season["local_id"],1,simkl_id="1001",mal_id="2001")

            repaired=dict(item); repaired["simkl_id"]="20"; repaired["mal_id"]="3000"
            season2=catalog.add_watchlist_season(series["local_id"],repaired,season_number=1)
            episode2=catalog.add_episode(season["local_id"],1,simkl_id="1002",mal_id="2002")

            self.assertEqual(season["local_id"],season2["local_id"])
            self.assertEqual("20",season2["simkl_id"])
            self.assertEqual("3000",season2["mal_id"])
            self.assertEqual(episode["local_id"],episode2["local_id"])
            self.assertEqual("1002",episode2["simkl_id"])
            self.assertEqual("2002",episode2["mal_id"])
        finally:
            for suffix in ("","-wal","-shm"):
                try: os.unlink(handle.name+suffix)
                except FileNotFoundError: pass

    def test_existing_season_cannot_be_silently_reparented(self):
        handle=tempfile.NamedTemporaryFile(delete=False); handle.close()
        try:
            store=WatchlistItemStore(handle.name); store.initialize()
            store.replace_provider_snapshot("anilist",[{
                "provider_item_id":"101506","ids":{"anilist":"101506"},
                "english_name":"UzaMaid!","list_status":"CURRENT","progress":1,
            }])
            item=store.list_all()[0]
            catalog=CatalogStore(handle.name,SegmentFactory()); catalog.initialize()
            first=catalog.get_or_create_series("UzaMaid!",root_simkl_id="1")
            season=catalog.add_watchlist_season(first["local_id"],item,season_number=1)
            second=catalog.get_or_create_series("Different Franchise",root_simkl_id="2")
            with self.assertRaises(ValueError):
                catalog.add_watchlist_season(second["local_id"],item,season_number=1)
            self.assertEqual(first["local_id"],season["local_id"][:6])
        finally:
            for suffix in ("","-wal","-shm"):
                try: os.unlink(handle.name+suffix)
                except FileNotFoundError: pass


if __name__=="__main__": unittest.main()
