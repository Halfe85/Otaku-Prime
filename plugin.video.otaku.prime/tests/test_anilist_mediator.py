import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.mediator_helper_anilist import (
    AniListMediatorClient,
    AniListMediatorHelper,
    _fuzzy_date_string,
)
from resources.lib.services.mediator_helper_simkl import MediatorMetadataPending
from resources.lib.services.mediator_tvshow import TVShowMediatorService


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


def media(media_id, title, media_format, episodes, prequel=None, year=2020,
          description=None, duration=24, status="FINISHED"):
    edges = []
    if prequel is not None:
        edges.append({
            "relationType": "PREQUEL",
            "node": {
                "id": prequel["id"],
                "type": "ANIME",
                "format": prequel["format"],
                "episodes": prequel["episodes"],
                "status": prequel.get("status"),
                "duration": prequel.get("duration"),
                "description": prequel.get("description"),
                "title": prequel["title"],
                "startDate": prequel["startDate"],
            },
        })
    return {
        "id": media_id,
        "type": "ANIME",
        "format": media_format,
        "episodes": episodes,
        "status": status,
        "duration": duration,
        "description": description,
        "title": {"english": title, "romaji": title + " Romaji", "native": None},
        "startDate": {"year": year, "month": 1, "day": 1},
        "relations": {"edges": edges},
    }


class FakeAniListClient:
    def __init__(self, rows, schedules=None, casts=None):
        self.rows = {str(row["id"]): row for row in rows}
        self.schedules = schedules or {}
        self.casts = casts or {}

    def media(self, anilist_id):
        return self.rows[str(anilist_id)]

    def schedule(self, anilist_id):
        return list(self.schedules.get(str(anilist_id), []))

    def cast(self, anilist_id):
        return list(self.casts.get(str(anilist_id), []))


class AniListMediatorTests(unittest.TestCase):
    def test_staff_fuzzy_dates_do_not_invent_missing_month_or_day(self):
        self.assertEqual("1980",_fuzzy_date_string({"year":1980}))
        self.assertEqual("1980-04",_fuzzy_date_string({"year":1980,"month":4}))
        self.assertEqual("1980-04-09",_fuzzy_date_string(
            {"year":1980,"month":4,"day":9}))

    def test_client_keeps_character_credits_and_standalone_staff_separate(self):
        client=AniListMediatorClient(opener=lambda *_args,**_kwargs: None)
        def query(source,_variables):
            if "characters(page:" in source:
                return {"Media":{"characters":{"pageInfo":{"hasNextPage":False},"edges":[{
                    "node":{"id":800,"name":{"full":"Hero"},"image":{"large":"hero.jpg"}},
                    "voiceActors":[{"id":700,"name":{"full":"Voice Actor"},
                                    "image":{"large":"actor.jpg"}}]}]}}}
            return {"Media":{"staff":{"pageInfo":{"hasNextPage":False},"edges":[{
                "role":"Director","node":{"id":900,"name":{"full":"Director"},
                                              "image":{"large":"director.jpg"}}}]}}}
        client._query=query

        credits=client.cast("10")

        self.assertEqual(2,len(credits))
        self.assertEqual("Hero",credits[0]["character"]["name"])
        self.assertEqual("Voice Actor",credits[0]["person"]["name"])
        self.assertEqual({},credits[1]["character"])
        self.assertEqual("Director",credits[1]["credit_type"])

    def test_client_preserves_character_results_when_staff_endpoint_fails(self):
        client=AniListMediatorClient(opener=lambda *_args,**_kwargs: None)
        def query(source,_variables):
            if "characters(page:" in source:
                return {"Media":{"characters":{"pageInfo":{"hasNextPage":False},"edges":[{
                    "node":{"id":800,"name":{"full":"Hero"}},"voiceActors":[]} ]}}}
            from resources.lib.services.mediator_helper_simkl import MediatorPlacementError
            raise MediatorPlacementError("timed out")
        client._query=query

        with self.assertLogs("otaku_prime.services-mediator_helper_anilist",level="WARNING"):
            credits=client.cast("10")

        self.assertEqual(1,len(credits))
        self.assertEqual("Hero",credits[0]["character"]["name"])

    def test_unreleased_third_season_returns_structural_placement_without_episodes(self):
        root = media(10, "How NOT to Summon a Demon Lord", "TV", 12, year=2018)
        second = media(20, "How NOT to Summon a Demon Lord Omega", "TV", 10,
                       prequel=root, year=2021)
        third = media(30, "Isekai Maou ULT", "TV", None, prequel=second,
                      status="NOT_YET_RELEASED")
        third["title"]["english"] = None
        third["startDate"] = {"year": None, "month": None, "day": None}
        helper = AniListMediatorHelper(FakeAniListClient([root, second, third]))

        with self.assertRaises(MediatorMetadataPending) as caught:
            helper.resolve({"anilist_id": "30", "episode_count": None,
                            "release_date": None})

        placement = caught.exception.placement
        self.assertIsNotNone(placement)
        self.assertEqual("How NOT to Summon a Demon Lord", placement["tv_show"]["name"])
        self.assertEqual(3, placement["season"]["number"])
        self.assertEqual("How NOT to Summon a Demon Lord Season 3",
                         placement["season"]["name"])
        self.assertEqual("NOT_YET_RELEASED", placement["season"]["release_status"])
        self.assertIsNone(placement["season"]["release_date"])
        self.assertEqual([], placement["episodes"])

    def test_tv_prequel_chain_becomes_numbered_seasons(self):
        root = media(10, "Example", "TV", 12, year=2020)
        second = media(20, "Example Season 2", "TV", 10, prequel=root, year=2022)
        helper = AniListMediatorHelper(FakeAniListClient([root, second]))

        result = helper.resolve({"anilist_id": "20", "episode_count": 10})

        self.assertEqual("10", result["tv_show"]["anilist_id"])
        self.assertEqual("TV", result["tv_show"]["source_format"])
        self.assertEqual(["10", "20"], result["relation_path"])
        self.assertEqual(2, result["season"]["number"])
        self.assertEqual((1, 10), (
            result["season"]["first_episode"], result["season"]["last_episode"]
        ))
        self.assertEqual(list(range(1, 11)), [
            row["episode_number"] for row in result["episodes"]
        ])

    def test_bottom_ova_remains_the_franchise_source(self):
        root = media(100, "Original OVA", "OVA", 2, year=1990)
        sequel = media(200, "Original OVA Part 2", "OVA", 3, prequel=root, year=1992)
        helper = AniListMediatorHelper(FakeAniListClient([root, sequel]))

        result = helper.resolve({"anilist_id": "200", "episode_count": 3})

        self.assertEqual("100", result["tv_show"]["anilist_id"])
        self.assertEqual("Original OVA", result["tv_show"]["name"])
        self.assertEqual("OVA", result["tv_show"]["source_format"])
        self.assertEqual("anilist_bottom_relation", result["tv_show"]["source"])
        self.assertEqual(["100", "200"], result["relation_path"])
        self.assertEqual(0, result["season"]["number"])
        self.assertEqual((3, 5), (
            result["season"]["first_episode"], result["season"]["last_episode"]
        ))
        self.assertEqual([3, 4, 5], [row["episode_number"] for row in result["episodes"]])

    def test_anilist_exposes_series_metadata_and_cast_for_library(self):
        root = media(10, "Example", "TV", 12, year=2020, description="Root description")
        current = media(
            20, "Example Season 2", "TV", 10, prequel=root, year=2022,
            description="Current season description", duration=25, status="RELEASING"
        )
        cast = [
            {"person_name": "Actor One", "character_name": "Hero", "sort_order": 0},
            {"person_name": "Actor Two", "character_name": "Rival", "sort_order": 1},
        ]
        helper = AniListMediatorHelper(
            FakeAniListClient([root, current], casts={"20": cast})
        )

        result = helper.resolve({"anilist_id": "20", "episode_count": 10})
        show = result["tv_show"]

        self.assertEqual(2020, show["publish_year"])
        self.assertEqual("Current season description", show["overview"])
        self.assertEqual(25, show["runtime_minutes"])
        self.assertEqual("RELEASING", show["air_status"])
        self.assertEqual(cast, show["cast"])
        self.assertTrue(all(row["runtime_minutes"] == 25 for row in result["episodes"]))

    def test_anilist_root_is_persisted_by_tvshow_mediator(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        try:
            watchlist = WatchlistItemStore(handle.name)
            watchlist.initialize()
            watchlist.replace_provider_snapshot("anilist", [{
                "provider_item_id": "200",
                "ids": {"anilist": "200"},
                "english_name": "Original OVA Part 2",
                "romaji_name": "Original OVA Part 2 Romaji",
                "media_format": "OVA",
                "episode_count": 3,
                "list_status": "PLANNING",
                "progress": 0,
                "raw": {},
            }])
            watchlist.finalize_merge()
            watchlist.mark_mediator_ready(watchlist.list_all()[0]["local_id"],True)
            catalog = CatalogStore(handle.name, SegmentFactory())
            catalog.initialize()
            root = media(100, "Original OVA", "OVA", 2, year=1990)
            sequel = media(
                200, "Original OVA Part 2", "OVA", 3, prequel=root, year=1992,
                description="OVA continuation", duration=28
            )
            helper = AniListMediatorHelper(FakeAniListClient(
                [root, sequel], casts={"200": [
                    {"person_name": "Actor", "character_name": "Character", "sort_order": 0}
                ]}
            ))
            service = TVShowMediatorService(
                watchlist, catalog, client=object(), helpers={"anilist": helper}
            )

            result = service.run_once()

            self.assertEqual(
                {"placed": 1, "existing": 0, "deferred": 0, "failed": 0}, result)
            series = catalog.list_series()[0]
            self.assertEqual("100", series["root_anilist_id"])
            self.assertEqual("anilist", series["source_provider"])
            self.assertEqual("OVA", series["source_media_format"])
            self.assertEqual(1990, series["publish_year"])
            self.assertEqual("OVA continuation", series["overview"])
            self.assertEqual(28, series["runtime_minutes"])
            detail = catalog.library_series_detail(series["local_id"])
            self.assertEqual("Actor", detail["cast"][0]["person_name"])
            self.assertEqual("Character", detail["cast"][0]["character_name"])
            season = catalog.list_seasons(series["local_id"])[0]
            self.assertEqual((0, 3, 5, "anilist"), (
                season["season_number"], season["first_episode"],
                season["last_episode"], season["provider_path"]
            ))
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(handle.name + suffix)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    unittest.main()
