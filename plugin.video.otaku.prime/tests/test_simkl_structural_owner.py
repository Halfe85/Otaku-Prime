import unittest

from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint
from resources.lib.services.mediator_structure import StructuralSimklMediatorEndpoint


class SimklStructureClient:
    def __init__(self):
        self.target = {
            "title": "BanG Dream! It's MyGO!!!!!",
            "en_title": "BanG Dream! It's MyGO!!!!!",
            "anime_type": "tv",
            "year": 2023,
            "ids": {"simkl": "200", "anilist": "163571", "tvdb": "433560"},
            "mapped_tvdb_seasons": [1],
            "relations": [
                {
                    "relation_type": "prequel",
                    "is_direct": True,
                    "anime_type": "tv",
                    "year": 2025,
                    "ids": {"simkl": "100"},
                }
            ],
        }
        self.root = {
            "title": "BanG Dream! Ave Mujica",
            "en_title": "BanG Dream! Ave Mujica",
            "anime_type": "tv",
            "year": 2025,
            "ids": {
                "simkl": "100", "anilist": "169295", "mal": "60000",
                "tvdb": "999999",
            },
            "relations": [],
        }

    def anime(self, simkl_id):
        value = str(simkl_id)
        if value == "200":
            return self.target
        if value == "100":
            return self.root
        raise AssertionError("unexpected Simkl anime id {}".format(value))

    def episodes(self, simkl_id):
        self.assert_target(simkl_id)
        return [
            {
                "type": "episode", "episode": 1, "title": "Episode 1",
                "ids": {"simkl_id": "e1"},
                "tvdb": {"season": 1, "episode": 1},
            },
            {
                "type": "episode", "episode": 2, "title": "Episode 2",
                "ids": {"simkl_id": "e2"},
                "tvdb": {"season": 1, "episode": 2},
            },
        ]

    @staticmethod
    def assert_target(simkl_id):
        if str(simkl_id) != "200":
            raise AssertionError("unexpected target id {}".format(simkl_id))

    def tv_franchise(self, anime_detail, root_detail=None):
        self.assertIsTarget(anime_detail)
        if root_detail is not self.root:
            raise AssertionError("relation root was not supplied separately")
        return {
            "name": "BanG Dream! It's MyGO!!!!!",
            "simkl_id": "2138098",
            "tvdb_id": "433560",
            "source": "simkl_tvdb_crossmap_validated",
        }

    def assertIsTarget(self, value):
        if value is not self.target:
            raise AssertionError("unexpected target payload")


class SimklStructuralOwnerTests(unittest.TestCase):
    def _item(self):
        return {
            "local_id": "watchlist-x",
            "simkl_id": "200",
            "anilist_id": "163571",
            "episode_count": 2,
            "media_format": "TV",
        }

    def test_endpoint_never_mixes_relation_root_with_target_tvdb_owner(self):
        result = SimklMediatorEndpoint(client=SimklStructureClient()).resolve(self._item())

        franchise = result["tv_show"]
        structure = result["structural_owner"]
        self.assertEqual("BanG Dream! Ave Mujica", franchise["name"])
        self.assertEqual("100", franchise["simkl_id"])
        self.assertEqual("169295", franchise["anilist_id"])
        self.assertEqual("999999", franchise["tvdb_id"])
        self.assertEqual(2025, franchise["publish_year"])

        self.assertEqual("BanG Dream! It's MyGO!!!!!", structure["name"])
        self.assertEqual("2138098", structure["simkl_id"])
        self.assertEqual("433560", structure["tvdb_id"])
        self.assertEqual(1, result["season"]["number"])
        self.assertEqual([1, 2], [row["episode_number"] for row in result["episodes"]])

    def test_structural_wrapper_preserves_same_separation(self):
        result = StructuralSimklMediatorEndpoint(
            client=SimklStructureClient()
        ).resolve(self._item())

        self.assertEqual("169295", result["tv_show"]["anilist_id"])
        self.assertEqual("999999", result["tv_show"]["tvdb_id"])
        self.assertEqual("433560", result["structural_owner"]["tvdb_id"])
        self.assertNotEqual(
            result["tv_show"]["tvdb_id"], result["structural_owner"]["tvdb_id"]
        )


if __name__ == "__main__":
    unittest.main()
