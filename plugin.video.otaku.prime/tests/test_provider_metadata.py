import unittest

from resources.lib.services.mediator_endpoint_kitsu import KitsuMediatorClient,KitsuMediatorEndpoint
from resources.lib.services.mediator_endpoint_mal import MALMediatorClient,MALMediatorEndpoint
from resources.lib.services.mediator_endpoint_simkl import _age_rating,_mature,_terms


class MALClient:
    def media(self,mal_id):
        return {"id":int(mal_id),"title":"Example","alternative_titles":{"en":"Example"},
                "start_date":"2020-01-01","media_type":"tv","status":"finished_airing",
                "num_episodes":1,"average_episode_duration":1440,"related_anime":[],
                "genres":[{"id":1,"name":"Action"},{"id":10,"name":"Fantasy"}],
                "rating":"r+","nsfw":"black"}


class KitsuClient:
    def anime(self,kitsu_id):
        return {"id":str(kitsu_id),"attributes":{
            "canonicalTitle":"Example","titles":{"en":"Example"},"subtype":"TV",
            "episodeCount":1,"episodeLength":24,"startDate":"2020-01-01",
            "status":"finished","ageRating":"R18"}}
    def prequels(self,_kitsu_id): return []
    def categories(self,_kitsu_id): return ["Action","Fantasy"]


class ProviderMetadataTests(unittest.TestCase):
    def test_mal_exposes_genres_age_rating_and_mature_flag(self):
        show=MALMediatorEndpoint(MALClient()).resolve({"mal_id":"1"})["tv_show"]
        self.assertEqual(["Action","Fantasy"],show["genres"])
        self.assertEqual("R+",show["age_rating"])
        self.assertTrue(show["mature"])

    def test_kitsu_exposes_categories_and_native_age_rating(self):
        show=KitsuMediatorEndpoint(KitsuClient()).resolve({"kitsu_id":"1"})["tv_show"]
        self.assertEqual(["Action","Fantasy"],show["genres"])
        self.assertEqual("R18",show["age_rating"])
        self.assertTrue(show["mature"])

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
