import unittest

from resources.lib.services.mediator_helper_simkl import MediatorPlacementError
from resources.lib.services.mediator_simkl_strict import StrictStructuralSimklMediatorEndpoint


class FakeClient:
    def __init__(self, target, episodes=None, owner=None):
        self.target = dict(target)
        self.episode_rows = list(episodes or [])
        self.owner = owner
        self.anime_calls = []
        self.tv_franchise_calls = []

    def anime(self, simkl_id):
        self.anime_calls.append(str(simkl_id))
        if str(simkl_id) != str((self.target.get("ids") or {}).get("simkl")):
            raise AssertionError("strict endpoint traversed away from the target Simkl item")
        return dict(self.target)

    def episodes(self, simkl_id):
        self.anime(simkl_id)
        return [dict(row) for row in self.episode_rows]

    def tv_franchise(self, target, root_detail=None):
        self.tv_franchise_calls.append((target, root_detail))
        return dict(self.owner) if self.owner else None


class FranchiseClient(FakeClient):
    def __init__(self, targets, episodes, owners):
        self.targets = {str(key): dict(value) for key, value in targets.items()}
        self.episode_rows = {str(key): list(value) for key, value in episodes.items()}
        self.owners = {str(key): dict(value) for key, value in owners.items()}
        self.anime_calls = []
        self.tv_franchise_calls = []

    def anime(self, simkl_id):
        key = str(simkl_id)
        self.anime_calls.append(key)
        return dict(self.targets[key])

    def episodes(self, simkl_id):
        return [dict(row) for row in self.episode_rows[str(simkl_id)]]

    def tv_franchise(self, target, root_detail=None):
        key = str((target.get("ids") or {}).get("simkl"))
        self.tv_franchise_calls.append((target, root_detail))
        value = self.owners.get(key)
        return dict(value) if value else None


def target(simkl_id="100", anime_type="tv", **extra):
    value = {
        "title": "Target Anime",
        "en_title": "Target Anime",
        "anime_type": anime_type,
        "year": 2024,
        "status": "ended",
        "ids": {"simkl": str(simkl_id), "anilist": "22", "mal": "33"},
        "relations": [
            {"relation_type": "prequel", "ids": {"simkl": "50"}, "title": "Completely Different"}
        ],
    }
    value.update(extra)
    return value


def owner(tvdb="74796"):
    return {
        "name": "TVDB Owner",
        "simkl_id": "900",
        "tvdb_id": tvdb,
        "source": "simkl_tvdb_crossmap_validated",
    }


def episode(source, season, number, ids=None):
    return {
        "episode": source,
        "type": "episode",
        "title": "Episode {}".format(source),
        "tvdb": {"season": season, "episode": number},
        "ids": dict(ids or {}),
    }


class StrictSimklMediatorTests(unittest.TestCase):
    def test_direct_mapped_season_relation_owns_later_cour(self):
        bleach = target(
            simkl_id="41066", mapped_tvdb_seasons=list(range(1, 17)),
            title="Bleach", en_title="Bleach",
            ids={"simkl": "41066", "tvdb": "74796", "anilist": "269", "mal": "269"},
            relations=[],
        )
        tybw = target(
            simkl_id="1300367", mapped_tvdb_seasons=[17],
            title="Bleach: Thousand-Year Blood War",
            ids={"simkl": "1300367", "tvdb": "458864", "anilist": "116674"},
            relations=[{
                "relation_type": "season 1", "is_direct": True,
                "ids": {"simkl": "41066"}, "title": "Bleach",
            }],
        )
        client = FranchiseClient(
            {"41066": bleach, "1300367": tybw},
            {"1300367": [episode(value, 17, value) for value in range(1, 14)]},
            {
                "41066": {
                    "name": "Bleach", "simkl_id": "41066",
                    "tvdb_id": "74796", "source": "simkl_tvdb_crossmap_validated",
                },
                "1300367": {
                    "name": "Bleach: Thousand-Year Blood War",
                    "simkl_id": "1300367", "tvdb_id": "458864",
                    "source": "simkl_tvdb_crossmap_validated",
                },
            },
        )

        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve({
            "local_id": "abcdef", "simkl_id": "1300367", "media_format": "TV"
        })

        self.assertEqual("Bleach", result["tv_show"]["name"])
        self.assertEqual("41066", result["tv_show"]["simkl_id"])
        self.assertEqual("74796", result["structural_owner"]["tvdb_id"])
        self.assertEqual(17, result["season"]["number"])
        self.assertEqual(list(range(1, 14)), [
            row["episode_number"] for row in result["episodes"]
        ])
        self.assertTrue(result["mediation_evidence"]["root_identity_verified"])
        self.assertEqual(
            "direct_mapped_season_relation",
            result["mediation_evidence"]["structural_owner_source"],
        )

    def test_target_relation_graph_is_not_traversed_for_ownership(self):
        client = FakeClient(
            target(),
            episodes=[episode(1, 17, 1), episode(2, 17, 2)],
            owner=owner(),
        )
        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve({
            "local_id": "abcdef", "simkl_id": "100", "media_format": "TV"
        })

        self.assertEqual(["100", "100"], client.anime_calls)
        self.assertEqual("100", result["relation_path"][0])
        self.assertEqual("74796", result["structural_owner"]["tvdb_id"])
        self.assertFalse(result["mediation_evidence"]["relation_traversal_used_for_ownership"])
        self.assertFalse(result["mediation_evidence"]["relation_traversal_used_for_season_number"])
        self.assertIs(client.tv_franchise_calls[0][0], client.tv_franchise_calls[0][1])

    def test_explicit_tvdb_coordinates_choose_season_without_prequel_position(self):
        client = FakeClient(
            target(mapped_tvdb_seasons=[]),
            episodes=[episode(1, 17, 1), episode(2, 17, 2)],
            owner=owner(),
        )
        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve({
            "local_id": "abcdef", "simkl_id": "100", "media_format": "TV"
        })

        self.assertEqual(17, result["season"]["number"])
        self.assertEqual("explicit_tvdb_coordinates", result["season"]["number_source"])
        self.assertEqual([1, 2], [row["episode_number"] for row in result["episodes"]])

    def test_one_simkl_item_can_map_across_multiple_tvdb_seasons(self):
        client = FakeClient(
            target(),
            episodes=[episode(1, 1, 11), episode(2, 1, 12), episode(3, 2, 1)],
            owner=owner(),
        )
        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve({
            "local_id": "abcdef", "simkl_id": "100", "media_format": "TV"
        })

        self.assertEqual([1, 2], [part["season"]["number"] for part in result["seasons"]])
        self.assertEqual([11, 12], [row["episode_number"] for row in result["seasons"][0]["episodes"]])
        self.assertEqual([1], [row["episode_number"] for row in result["seasons"][1]["episodes"]])

    def test_non_movie_without_tvdb_owner_is_rejected(self):
        client = FakeClient(
            target(), episodes=[episode(1, 1, 1)], owner=None
        )
        with self.assertRaises(MediatorPlacementError) as caught:
            StrictStructuralSimklMediatorEndpoint(client=client).resolve({
                "local_id": "abcdef", "simkl_id": "100", "media_format": "TV"
            })
        self.assertIn("no TVDB structural series owner", str(caught.exception))

    def test_standalone_movie_can_remain_in_movies_without_tvdb_owner(self):
        client = FakeClient(target(anime_type="movie"), owner=None)
        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve({
            "local_id": "abcdef", "simkl_id": "100", "media_format": "MOVIE"
        })

        self.assertEqual("movie", result["library_type"])
        self.assertIsNone(result["structural_owner"])
        self.assertEqual([], result["episodes"])
        self.assertEqual("standalone_simkl_movie", result["season"]["number_source"])

    def test_referenced_special_requires_exact_external_id_evidence(self):
        client = FakeClient(
            target(),
            episodes=[episode(1, 0, 5, ids={})],
            owner=owner(),
        )
        item = {
            "local_id": "abcdef",
            "simkl_reference_id": "100",
            "special_locator": "S00E05",
            "mal_id": "123",
            "media_format": "SPECIAL",
        }

        with self.assertRaises(MediatorPlacementError) as caught:
            StrictStructuralSimklMediatorEndpoint(client=client).resolve(item)

        self.assertIn("no exact AniList/MAL/Kitsu ID evidence", str(caught.exception))

    def test_referenced_special_accepts_exact_external_id_evidence(self):
        client = FakeClient(
            target(),
            episodes=[episode(1, 0, 5, ids={"mal": "123"})],
            owner=owner(),
        )
        item = {
            "local_id": "abcdef",
            "simkl_reference_id": "100",
            "special_locator": "S00E05",
            "mal_id": "123",
            "media_format": "SPECIAL",
        }

        result = StrictStructuralSimklMediatorEndpoint(client=client).resolve(item)

        self.assertEqual(0, result["season"]["number"])
        self.assertEqual(5, result["episodes"][0]["episode_number"])
        self.assertEqual(
            "watchlist_special_locator_exact_id_verified",
            result["season"]["number_source"],
        )
        self.assertEqual(
            "mal",
            result["mediation_evidence"]["special_exact_id_matches"][0]["provider"],
        )


if __name__ == "__main__":
    unittest.main()
