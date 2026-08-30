import unittest

from resources.lib.services.mediator_endpoint_anilist import AniListMediatorEndpoint
from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorClient,KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorClient,MALMediatorEndpoint
from resources.lib.services.mediator_endpoint_simkl import (
    SimklMediatorEndpoint,_age_rating,_mature,_terms)
from resources.lib.services.mediator_helper_simkl import MediatorMetadataPending


class MALClient:
    def media(self,mal_id):
        return {"id":int(mal_id),"title":"Example","alternative_titles":{"en":"Example"},
                "start_date":"2020-01-01","media_type":"tv","status":"finished_airing",
                "num_episodes":1,"average_episode_duration":1440,"related_anime":[],
                "main_picture":{"large":"https://img.example/mal.jpg"},
                "genres":[{"id":1,"name":"Action"},{"id":10,"name":"Fantasy"}],
                "rating":"r+","nsfw":"black"}


class MALGraphClient:
    def __init__(self,rows): self.rows={str(row["id"]):row for row in rows}
    def media(self,mal_id): return self.rows[str(mal_id)]


def mal_media(media_id,title,media_type,episodes,year,relations=None):
    return {"id":media_id,"title":title,"alternative_titles":{"en":title},
            "start_date":"{}-01-01".format(year),"media_type":media_type,
            "status":"finished_airing","num_episodes":episodes,
            "average_episode_duration":1440,"related_anime":relations or [],
            "genres":[],"rating":"pg_13","nsfw":"white"}


def mal_relation(relation_type,row):
    return {"relation_type":relation_type,
            "node":{"id":row["id"],"title":row["title"]}}


class KitsuClient:
    def anime(self,kitsu_id):
        return {"id":str(kitsu_id),"attributes":{
            "canonicalTitle":"Example","titles":{"en":"Example"},"subtype":"TV",
            "episodeCount":1,"episodeLength":24,"startDate":"2020-01-01",
            "status":"finished","ageRating":"R18",
            "posterImage":{"original":"https://img.example/kitsu.jpg"}}}
    def prequels(self,_kitsu_id): return []
    def categories(self,_kitsu_id): return ["Action","Fantasy"]


class ProviderMetadataTests(unittest.TestCase):
    def test_provider_poster_extractors_return_browser_urls(self):
        anilist_client=type("AniListClient",(),{
            "media":lambda _self,_value:{
                "coverImage":{"extraLarge":"https://img.example/anilist.jpg"}}})()
        simkl_client=type("SimklClient",(),{
            "anime":lambda _self,_value:{"poster":"poster-hash"}})()

        self.assertEqual(
            "https://img.example/anilist.jpg",
            AniListMediatorEndpoint(anilist_client).poster("1"))
        self.assertEqual(
            "https://img.example/mal.jpg",MALMediatorEndpoint(MALClient()).poster("1"))
        self.assertEqual(
            "https://img.example/kitsu.jpg",KitsuMediatorEndpoint(KitsuClient()).poster("1"))
        self.assertEqual(
            "https://simkl.in/posters/poster-hash_m.jpg",
            SimklMediatorEndpoint(simkl_client).poster("1"))

    def test_mal_exposes_genres_age_rating_and_mature_flag(self):
        show=MALMediatorEndpoint(MALClient()).resolve({"mal_id":"1"})["tv_show"]
        self.assertEqual(["Action","Fantasy"],show["genres"])
        self.assertEqual("R+",show["age_rating"])
        self.assertTrue(show["mature"])

    def test_mal_movie_parent_is_stored_below_parent_franchise(self):
        bleach=mal_media(269,"Bleach","tv",366,2004)
        movie=mal_media(1686,"Bleach Movie 1","movie",1,2006,
                        [mal_relation("parent_story",bleach)])

        result=MALMediatorEndpoint(MALGraphClient([bleach,movie])).resolve(
            {"mal_id":"1686"})

        self.assertEqual("269",result["tv_show"]["mal_id"])
        self.assertEqual("Bleach",result["tv_show"]["name"])
        self.assertEqual("series",result["library_type"])
        self.assertEqual(0,result["season"]["number"])
        self.assertEqual(["1686","269"],result["franchise_relation_path"])

    def test_mal_special_bridge_uses_alternative_setting_franchise(self):
        bleach=mal_media(269,"Bleach","tv",366,2004)
        burn=mal_media(41468,"Burn the Witch","ona",3,2020,
                       [mal_relation("alternative_setting",bleach)])
        special=mal_media(56671,"Burn the Witch #0.8","tv_special",1,2023,
                          [mal_relation("sequel",burn)])

        result=MALMediatorEndpoint(MALGraphClient([bleach,burn,special])).resolve(
            {"mal_id":"56671"})

        self.assertEqual("269",result["tv_show"]["mal_id"])
        self.assertEqual("Bleach",result["tv_show"]["name"])
        self.assertEqual(0,result["season"]["number"])
        self.assertEqual(["56671","41468","269"],
                         result["franchise_relation_path"])

    def test_mal_standalone_movie_uses_movie_library(self):
        movie=mal_media(20954,"A Silent Voice","movie",1,2016)

        result=MALMediatorEndpoint(MALGraphClient([movie])).resolve({"mal_id":"20954"})

        self.assertEqual("movie",result["library_type"])

    def test_mal_preserves_confirmed_structure_when_episode_count_is_missing(self):
        television=mal_media(100,"Announced Series","tv",None,2027)

        with self.assertRaises(MediatorMetadataPending) as caught:
            MALMediatorEndpoint(MALGraphClient([television])).resolve(
                {"mal_id":"100","episode_count":None,"release_date":"2027-01-01"})

        self.assertEqual("Announced Series",caught.exception.placement["tv_show"]["name"])
        self.assertEqual("2027-01-01",caught.exception.placement["season"]["release_date"])
        self.assertEqual([],caught.exception.placement["episodes"])

    def test_mal_standalone_movie_without_episode_count_is_complete(self):
        movie=mal_media(20954,"A Silent Voice","movie",None,2016)

        result=MALMediatorEndpoint(MALGraphClient([movie])).resolve(
            {"mal_id":"20954","episode_count":None})

        self.assertEqual("movie",result["library_type"])
        self.assertEqual([],result["episodes"])

    def test_kitsu_exposes_categories_and_native_age_rating(self):
        show=KitsuMediatorEndpoint(KitsuClient()).resolve({"kitsu_id":"1"})["tv_show"]
        self.assertEqual(["Action","Fantasy"],show["genres"])
        self.assertEqual("R18",show["age_rating"])
        self.assertTrue(show["mature"])

    def test_kitsu_preserves_confirmed_structure_when_episode_count_is_missing(self):
        class PendingKitsuClient(KitsuClient):
            def anime(self,kitsu_id):
                row=super().anime(kitsu_id)
                row["attributes"]["episodeCount"]=None
                row["attributes"]["startDate"]="2027-04-01"
                return row

        with self.assertRaises(MediatorMetadataPending) as caught:
            KitsuMediatorEndpoint(PendingKitsuClient()).resolve(
                {"kitsu_id":"1","episode_count":None,"release_date":"2027-04-01"})

        self.assertEqual("Example",caught.exception.placement["tv_show"]["name"])
        self.assertEqual("2027-04-01",caught.exception.placement["season"]["release_date"])
        self.assertEqual([],caught.exception.placement["episodes"])

    def test_kitsu_standalone_movie_without_episode_count_is_complete(self):
        class MovieKitsuClient(KitsuClient):
            def anime(self,kitsu_id):
                row=super().anime(kitsu_id)
                row["attributes"]["subtype"]="movie"
                row["attributes"]["episodeCount"]=None
                return row

        result=KitsuMediatorEndpoint(MovieKitsuClient()).resolve(
            {"kitsu_id":"1","episode_count":None})

        self.assertEqual("movie",result["library_type"])
        self.assertEqual([],result["episodes"])

    def test_simkl_accepts_native_genres_themes_and_certification(self):
        values=_terms({"genres":["Action"],"themes":[{"name":"Isekai"}]},
                      {"genres":["Action","Fantasy"]})
        rating=_age_rating({"certification":"18+"})
        self.assertEqual(["Action","Fantasy"],values["genres"])
        self.assertEqual(["Isekai"],values["themes"])
        self.assertEqual("18+",rating)
        self.assertTrue(_mature(rating,{}))

    def test_mal_staff_data_normalizes_people_characters_and_standalone_staff(self):
        client=MALMediatorClient(opener=lambda *_args,**_kwargs: None)
        def get(url):
            if url.endswith("/characters"):
                return {"data":[{"character":{"mal_id":20,"name":"Hero",
                  "images":{"jpg":{"image_url":"hero.jpg"}}},"voice_actors":[{
                  "person":{"mal_id":10,"name":"Actor","images":{"jpg":{"image_url":"actor.jpg"}}},
                  "language":"Japanese"}]}]}
            return {"data":[{"person":{"mal_id":30,"name":"Director"},
                             "positions":["Director"]}]}
        client._json=get

        cast=client.cast("1")

        self.assertEqual(2,len(cast))
        self.assertEqual(("Actor","Hero","mal"),(
            cast[0]["person"]["name"],cast[0]["character"]["name"],
            cast[0]["source_provider"]))
        self.assertEqual("Director",cast[1]["credit_type"])

    def test_kitsu_staff_data_normalizes_included_castings(self):
        client=KitsuMediatorClient(opener=lambda *_args,**_kwargs: None)
        client._json=lambda _url:{"included":[
            {"type":"castings","id":"1","attributes":{"role":"voice_actor"},
             "relationships":{"person":{"data":{"type":"people","id":"10"}},
                              "character":{"data":{"type":"characters","id":"20"}}}},
            {"type":"people","id":"10","attributes":{"name":"Actor",
             "image":{"original":"actor.jpg"}}},
            {"type":"characters","id":"20","attributes":{"name":"Hero",
             "description":"Lead","image":{"original":"hero.jpg"}}},
        ]}

        cast=client.cast("1")

        self.assertEqual(1,len(cast))
        self.assertEqual(("10","20","kitsu"),(
            str(cast[0]["person"]["provider_id"]),
            str(cast[0]["character"]["provider_id"]),cast[0]["source_provider"]))


if __name__=="__main__": unittest.main()
