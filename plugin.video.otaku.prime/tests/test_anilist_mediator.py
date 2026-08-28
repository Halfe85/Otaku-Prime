import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.mediator_helper_anilist import AniListMediatorHelper
from resources.lib.services.mediator_tvshow import TVShowMediatorService


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


def media(media_id, title, media_format, episodes, prequel=None, year=2020):
    edges = []
    if prequel is not None:
        edges.append({
            "relationType": "PREQUEL",
            "node": {
                "id": prequel["id"],
                "type": "ANIME",
                "format": prequel["format"],
                "episodes": prequel["episodes"],
                "title": prequel["title"],
                "startDate": prequel["startDate"],
            },
        })
    return {
        "id": media_id,
        "type": "ANIME",
        "format": media_format,
        "episodes": episodes,
        "title": {"english": title, "romaji": title + " Romaji", "native": None},
        "startDate": {"year": year, "month": 1, "day": 1},
        "relations": {"edges": edges},
    }


class FakeAniListClient:
    def __init__(self, rows, schedules=None):
        self.rows = {str(row["id"]): row for row in rows}
        self.schedules = schedules or {}

    def media(self, anilist_id):
        return self.rows[str(anilist_id)]

    def schedule(self, anilist_id):
        return list(self.schedules.get(str(anilist_id), []))


class AniListMediatorTests(unittest.TestCase):
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
            catalog = CatalogStore(handle.name, SegmentFactory())
            catalog.initialize()
            root = media(100, "Original OVA", "OVA", 2, year=1990)
            sequel = media(200, "Original OVA Part 2", "OVA", 3, prequel=root, year=1992)
            helper = AniListMediatorHelper(FakeAniListClient([root, sequel]))
            service = TVShowMediatorService(
                watchlist, catalog, client=object(), helpers={"anilist": helper}
            )

            result = service.run_once()

            self.assertEqual({"placed": 1, "existing": 0, "failed": 0}, result)
            series = catalog.list_series()[0]
            self.assertEqual("100", series["root_anilist_id"])
            self.assertEqual("anilist", series["source_provider"])
            self.assertEqual("OVA", series["source_media_format"])
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
