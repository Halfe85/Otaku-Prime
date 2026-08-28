import unittest

from resources.lib.services.mediator_processor import MediatorProcessor


class Endpoint:
    def __init__(self,name,result=None,error=None): self.name=name; self.result=result; self.error=error; self.calls=0
    def available(self,item): return item.get(self.name+"_id") is not None
    def resolve(self,item):
        self.calls+=1
        if self.error: raise RuntimeError(self.error)
        return dict(self.result)


def placement(provider,season=1):
    return {"provider_path":provider,"provider_id":provider,
      "tv_show":{"name":"Show","romaji_name":"Show"},
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

    def test_anilist_and_mal_are_combined_when_simkl_fails(self):
        endpoints={"simkl":Endpoint("simkl",error="down"),"anilist":Endpoint("anilist",placement("anilist")),
                   "mal":Endpoint("mal",placement("mal")),"kitsu":Endpoint("kitsu",placement("kitsu"))}
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("anilist+mal",result["provider_path"])
        self.assertEqual(["anilist","mal"],result["provider_consensus"])
        self.assertEqual(0,endpoints["kitsu"].calls)

    def test_kitsu_is_last_after_simkl_and_anilist_mal_fail(self):
        endpoints={name:Endpoint(name,error="no") for name in ("simkl","anilist","mal")}
        endpoints["kitsu"]=Endpoint("kitsu",placement("kitsu"))
        result=MediatorProcessor(endpoints=endpoints).resolve(self.item())
        self.assertEqual("kitsu",result["provider_path"]); self.assertEqual(1,endpoints["kitsu"].calls)


if __name__=="__main__": unittest.main()
