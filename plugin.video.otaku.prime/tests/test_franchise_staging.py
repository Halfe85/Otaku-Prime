import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.database.watchlist_relations import WatchlistRelationStore
from resources.lib.services.anilist_relations import AniListFranchiseResolverService
from resources.lib.users import UserStore


class KonoSubaLikeRelations:
    """TV season 3 reaches the root through a movie PREQUEL bridge."""

    media = {
        "10": {
            "id": 10,
            "format": "TV",
            "title": {"english": "Example Franchise", "romaji": "Example"},
            "startDate": {"year": 2016, "month": 1, "day": 14},
            "relations": {"edges": []},
        },
        "11": {
            "id": 11,
            "format": "TV",
            "title": {"english": "Example Franchise 2", "romaji": "Example 2"},
            "startDate": {"year": 2017, "month": 1, "day": 12},
            "relations": {"edges": [
                {"relationType": "PREQUEL", "node": {
                    "id": 10, "format": "TV",
                    "title": {"english": "Example Franchise", "romaji": "Example"},
                    "startDate": {"year": 2016, "month": 1, "day": 14},
                }}
            ]},
        },
        "20": {
            "id": 20,
            "format": "MOVIE",
            "title": {"english": "Example Movie", "romaji": "Example Movie"},
            "startDate": {"year": 2019, "month": 8, "day": 30},
            "relations": {"edges": [
                {"relationType": "PREQUEL", "node": {
                    "id": 11, "format": "TV",
                    "title": {"english": "Example Franchise 2", "romaji": "Example 2"},
                    "startDate": {"year": 2017, "month": 1, "day": 12},
                }},
                {"relationType": "SEQUEL", "node": {
                    "id": 12, "format": "TV",
                    "title": {"english": "Example Franchise 3", "romaji": "Example 3"},
                    "startDate": {"year": 2024, "month": 4, "day": 10},
                }},
            ]},
        },
        "12": {
            "id": 12,
            "format": "TV",
            "title": {"english": "Example Franchise 3", "romaji": "Example 3"},
            "startDate": {"year": 2024, "month": 4, "day": 10},
            "relations": {"edges": [
                {"relationType": "PREQUEL", "node": {
                    "id": 20, "format": "MOVIE",
                    "title": {"english": "Example Movie", "romaji": "Example Movie"},
                    "startDate": {"year": 2019, "month": 8, "day": 30},
                }}
            ]},
        },
    }

    def fetch_many(self, ids):
        return [self.media[str(value)] for value in ids]


class ParentSpecialRelations:
    media = {
        "30": {
            "id": 30,
            "format": "OVA",
            "title": {"english": "Example Bonus", "romaji": "Example Bonus"},
            "startDate": {"year": 2018, "month": 1, "day": 1},
            "relations": {"edges": [
                {"relationType": "PARENT", "node": {
                    "id": 10, "format": "TV",
                    "title": {"english": "Example Franchise", "romaji": "Example"},
                    "startDate": {"year": 2016, "month": 1, "day": 14},
                }}
            ]},
        },
        "10": KonoSubaLikeRelations.media["10"],
    }

    def fetch_many(self, ids):
        return [self.media[str(value)] for value in ids]


class FranchiseStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "users.sqlite")
        UserStore(self.db_path).initialize()
        self.media = WatchlistMediaStore(self.db_path)
        self.media.initialize()
        self.relations = WatchlistRelationStore(self.db_path)
        self.relations.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def _stage(self, anilist_id, title, media_format):
        self.media.replace_anilist_staging([{
            "anilist_id": anilist_id,
            "english_name": title,
            "romaji_name": title,
            "list_status": "CURRENT",
            "progress": 0,
            "media_format": media_format,
            "release_date": "2024-04-10",
        }])

    def test_tv_movie_tv_chain_finds_oldest_tv_root_without_promoting_item(self):
        self._stage(12, "Example Franchise 3", "TV")
        result = AniListFranchiseResolverService(
            self.media,
            client=KonoSubaLikeRelations(),
            stage_only=True,
        ).run_once()

        self.assertEqual(1, result["resolved"])
        self.assertEqual([], result["failed"])
        self.assertTrue(result["staged_only"])

        staged = self.relations.get(12)
        self.assertEqual("10", staged["relation_root_id"])
        self.assertEqual("Example Franchise", staged["franchise_english_name"])
        self.assertEqual("2016-01-14", staged["franchise_release_date"])
        self.assertEqual(1, staged["relation_resolved"])
        self.assertEqual(["10", "11", "20", "12"], json.loads(staged["relation_path_json"]))

        franchises = self.media.list_media("series")
        self.assertEqual(1, len(franchises))
        self.assertEqual("10", franchises[0]["anilist_root_id"])
        self.assertEqual("Example Franchise", franchises[0]["english_name"])

        # Provider placement has not happened yet.
        self.assertEqual([], self.media.list_media("season"))
        self.assertEqual([], self.media.list_media("episode"))

    def test_movie_does_not_use_its_sequel_as_the_franchise_root(self):
        self._stage(20, "Example Movie", "MOVIE")
        result = AniListFranchiseResolverService(
            self.media,
            client=KonoSubaLikeRelations(),
            stage_only=True,
        ).run_once()

        self.assertEqual([], result["failed"])
        staged = self.relations.get(20)
        self.assertEqual("10", staged["relation_root_id"])
        self.assertEqual("Example Franchise", staged["franchise_english_name"])
        self.assertNotEqual("12", staged["relation_root_id"])
        self.assertEqual("movie", staged["media_category"])
        self.assertEqual([], self.media.list_media("season"))

    def test_parent_ova_is_attached_to_parent_franchise_but_stays_in_watchlist(self):
        self._stage(30, "Example Bonus", "OVA")
        result = AniListFranchiseResolverService(
            self.media,
            client=ParentSpecialRelations(),
            stage_only=True,
        ).run_once()

        self.assertEqual([], result["failed"])
        staged = self.relations.get(30)
        self.assertEqual("10", staged["relation_root_id"])
        self.assertEqual("PARENT", staged["relation_type"])
        self.assertEqual("ova", staged["media_category"])
        self.assertEqual([], self.media.list_media("season"))


if __name__ == "__main__":
    unittest.main()
