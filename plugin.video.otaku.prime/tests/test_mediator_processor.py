import unittest

from resources.lib.services.mediator_processor import MediatorProcessor
from resources.lib.services.mediator_helper_simkl import MediatorMetadataPending


class Endpoint:
    def __init__(self,name,result=None,error=None): self.name=name; self.result=result; self.error=error; self.calls=0
    def available(self,item): return item.get(self.name+"_id") is not None
    def resolve(self,item):
        self.calls+=1
        if self.error: raise RuntimeError(self.error)
        return dict(self.result)


class PendingEndpoint(Endpoint):
    def resolve(self,item):
        self.calls+=1
        raise MediatorMetadataPending(
            self.error or self.name+" episode metadata pending",
            placement=self.result)


class FranchiseEndpoint(Endpoint):
    def __init__(self,name,result=None,error=None,identity=None):
        super().__init__(name,result,error); self.identity=identity; self.identity_calls=[]
    def franchise_identity(self,provider_id):
        self.identity_calls.append(str(provider_id)); return dict(self.identity)


def placement(provider,season=1):
    return {"provider_path":provider,"provider_id":provider,"tv_show":{"name":"Show","romaji_name":"Show"},
      "season":{"number":season,"number_source":provider,"name":"Show","first_episode":1,"last_episode":2},
      "episodes":[{"source_episode_number":1,"episode_number":1,"season_number":season},
                  {"source_episode_number":2,"episode_number":2,"season_number":season}]}


class MediatorProcessorTests(unittest.TestCase):
    def item(self): return {"local_id":"x","simkl_id":"1","anilist_id":"2","mal_id":"3","kitsu_id":"4"}

    def test_simkl_wins_without_calling_other_endpoints(self):
        endpoints={name:Endpoint(name,placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("simkl",result["provider_path"])
        self.assertEqual((1,0,0,0),tuple(endpoints[name].calls for name in ("simkl","anilist","mal","kitsu")))

    def test_simkl_coordinates_use_anilist_canonical_franchise_identity(self):
        simkl=placement("simkl",season=0)
        simkl["tv_show"].update({"name":"Bleach the Movie","simkl_id":"41066",
                                  "tvdb_id":"74796"})
        endpoints={
            "simkl":Endpoint("simkl",simkl),
            "anilist":FranchiseEndpoint("anilist",placement("anilist"),identity={
                "name":"Bleach","romaji_name":"BLEACH","anilist_id":"269",
                "mal_id":"269","source_format":"TV","publish_year":2004,
                "source":"anilist_franchise_relation",
                "franchise_relation_path":["1686","269"]}),
            "mal":Endpoint("mal",placement("mal")),
            "kitsu":Endpoint("kitsu",placement("kitsu")),
        }

        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())

        self.assertEqual("simkl",result["provider_path"])
        self.assertEqual(0,result["season"]["number"])
        self.assertEqual("Bleach",result["tv_show"]["name"])
        self.assertEqual("269",result["tv_show"]["anilist_id"])
        self.assertEqual("41066",result["tv_show"]["simkl_id"])
        self.assertEqual("74796",result["tv_show"]["tvdb_id"])
        self.assertEqual(["2"],endpoints["anilist"].identity_calls)
        self.assertEqual(0,endpoints["anilist"].calls)

    def test_conflicted_stored_simkl_id_is_bypassed(self):
        endpoints={name:Endpoint(name,placement(name)) for name in ("simkl","anilist","mal","kitsu")}
        item=self.item(); item["identity_resolution_status"]="CONFLICT_EXACT"
        result=MediatorProcessor(endpoints=endpoints).resolve(item)
        self.assertEqual("anilist+mal",result["provider_path"]); self.assertEqual(0,endpoints["simkl"].calls)

    def test_anilist_and_mal_are_combined_when_simkl_fails(self):
        endpoints={"simkl":Endpoint("simkl",error="down"),"anilist":Endpoint("anilist",placement("anilist")),
                   "mal":Endpoint("mal",placement("mal")),"kitsu":Endpoint("kitsu",placement("kitsu"))}
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("anilist+mal",result["provider_path"]); self.assertEqual(["anilist","mal"],result["provider_consensus"])
        self.assertEqual(0,endpoints["kitsu"].calls)

    def test_anilist_and_mal_metadata_lists_are_combined_and_mature_is_orred(self):
        anilist=placement("anilist"); mal=placement("mal")
        anilist["tv_show"].update({"genres":["Action"],"themes":["Isekai"],
                                    "mature":False})
        mal["tv_show"].update({"genres":["Action","Fantasy"],"themes":[],
                                "age_rating":"R+","mature":True})
        endpoints={"simkl":Endpoint("simkl",error="down"),
                   "anilist":Endpoint("anilist",anilist),"mal":Endpoint("mal",mal),
                   "kitsu":Endpoint("kitsu",error="unused")}

        show=MediatorProcessor(endpoints=endpoints).resolve(self.item())["tv_show"]

        self.assertEqual(["Action","Fantasy"],show["genres"])
        self.assertEqual(["Isekai"],show["themes"])
        self.assertEqual("R+",show["age_rating"])
        self.assertTrue(show["mature"])

    def test_anilist_composite_accepts_partial_mal_episode_coverage(self):
        anilist=placement("anilist",season=0)
        mal=placement("mal",season=0); mal["episodes"]=mal["episodes"][:1]
        mal["season"]["last_episode"]=1
        endpoints={"simkl":Endpoint("simkl",error="not listed"),
                   "anilist":Endpoint("anilist",anilist),"mal":Endpoint("mal",mal),
                   "kitsu":Endpoint("kitsu",error="not listed")}
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("anilist+mal",result["provider_path"])
        self.assertEqual([1,2],[row["episode_number"] for row in result["episodes"]])
        self.assertEqual("partial_episode_coverage",result["provider_consensus_scope"])
        self.assertEqual({"anilist":[1,2],"mal":[1]},result["provider_coverage"])
        self.assertEqual(0,endpoints["kitsu"].calls)

    def test_anilist_remains_authoritative_when_mal_numbers_specials_differently(self):
        anilist=placement("anilist",season=0)
        mal=placement("mal",season=0)
        for row in mal["episodes"]: row["episode_number"]+=1
        endpoints={"simkl":Endpoint("simkl",error="not listed"),
                   "anilist":Endpoint("anilist",anilist),"mal":Endpoint("mal",mal),
                   "kitsu":Endpoint("kitsu",placement("kitsu",season=0))}
        with self.assertLogs("otaku_prime.services-mediator_processor",level="INFO") as logs:
            result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("anilist",result["provider_path"])
        self.assertIn("different",result["provider_disagreement"]["error"])
        self.assertTrue(any(
            "INFO:otaku_prime.services-mediator_processor:MAL alternate coordinates ignored"
            in message for message in logs.output))
        self.assertEqual(0,endpoints["kitsu"].calls)

    def test_anilist_remains_authoritative_when_mal_classifies_season_zero_differently(self):
        anilist=placement("anilist",season=0); mal=placement("mal",season=2)
        endpoints={"simkl":Endpoint("simkl",error="not listed"),
                   "anilist":Endpoint("anilist",anilist),"mal":Endpoint("mal",mal),
                   "kitsu":Endpoint("kitsu",placement("kitsu",season=2))}
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("anilist",result["provider_path"])
        self.assertEqual(0,result["season"]["number"])
        self.assertEqual(0,endpoints["kitsu"].calls)

    def test_kitsu_is_last_after_simkl_and_anilist_mal_fail(self):
        endpoints={name:Endpoint(name,error="no") for name in ("simkl","anilist","mal")}; endpoints["kitsu"]=Endpoint("kitsu",placement("kitsu"))
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("kitsu",result["provider_path"]); self.assertEqual(1,endpoints["kitsu"].calls)

    def test_all_unknown_episode_counts_are_deferred_not_failed(self):
        endpoints={name:PendingEndpoint(name,error="no episode count")
                   for name in ("simkl","anilist","mal","kitsu")}
        with self.assertRaises(MediatorMetadataPending) as caught:
            MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertIn("not been published",str(caught.exception))
        self.assertEqual((1,1,1,1),tuple(
            endpoints[name].calls for name in ("simkl","anilist","mal","kitsu")))

    def test_anilist_structural_position_survives_other_pending_providers(self):
        structural=placement("anilist",season=3)
        structural["episodes"]=[]
        structural["season"].update({"first_episode":None,"last_episode":None,
                                     "release_status":"NOT_YET_RELEASED"})
        endpoints={
            "simkl":PendingEndpoint("simkl",error="no episodes"),
            "anilist":PendingEndpoint("anilist",result=structural,error="no episode count"),
            "mal":PendingEndpoint("mal",error="no episode count"),
            "kitsu":PendingEndpoint("kitsu",error="no episode count"),
        }

        with self.assertRaises(MediatorMetadataPending) as caught:
            MediatorProcessor(endpoints=endpoints).resolve(self.item())

        self.assertEqual(3,caught.exception.placement["season"]["number"])
        self.assertEqual("anilist",caught.exception.placement["provider_path"])

    def test_mal_structure_survives_when_simkl_and_anilist_cannot_resolve(self):
        structural=placement("mal",season=2)
        structural["episodes"]=[]
        structural["season"].update({"first_episode":None,"last_episode":None,
                                     "release_date":"2027-10-01"})
        endpoints={
            "simkl":Endpoint("simkl",error="franchise not found"),
            "anilist":Endpoint("anilist",error="relation graph unavailable"),
            "mal":PendingEndpoint("mal",result=structural,error="no episode count"),
            "kitsu":PendingEndpoint("kitsu",error="no episode count"),
        }

        with self.assertRaises(MediatorMetadataPending) as caught:
            MediatorProcessor(endpoints=endpoints).resolve(self.item())

        self.assertEqual("mal",caught.exception.placement["provider_path"])
        self.assertEqual(2,caught.exception.placement["season"]["number"])
        self.assertEqual("2027-10-01",
                         caught.exception.placement["season"]["release_date"])


if __name__=="__main__": unittest.main()
