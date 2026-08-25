import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.services.metadata_structure_resolver import (
    MetadataStructureResolverService,
)


class MetadataStructurePlacementTests(unittest.TestCase):
    @staticmethod
    def _uzamaid_structure():
        regular = [
            {"id": 1000 + number, "number": number,
             "name": "Episode {}".format(number),
             "air_date": "2018-10-{:02d}".format(4 + number)}
            for number in range(1, 13)
        ]
        return {
            "show": {"id": "uzamaid", "name": "UzaMaid!", "year": 2018},
            "seasons": [
                {
                    "id": "s0", "number": 0, "name": "Specials",
                    "air_date": "2019-04-24",
                    "episodes": [{
                        "id": 9001, "number": 1,
                        "name": "My Maid Is Still Seriously Way Too Annoying...",
                        "air_date": "2019-04-24",
                    }],
                },
                {
                    "id": "s1", "number": 1, "name": "Season 1",
                    "air_date": "2018-10-05", "episodes": regular,
                },
            ],
        }

    def test_anilist_101506_main_tv_is_placed_as_provider_season_one(self):
        item = {
            "provider": "anilist",
            "provider_item_id": "101506",
            "english_name": "UzaMaid!",
            "romaji_name": "Uchi no Maid ga Uzasugiru!",
            "native_name": "うちのメイドがウザすぎる！",
            "media_format": "TV",
            "release_date": "2018-10-05",
            "episode_count": 12,
        }
        structure = self._uzamaid_structure()
        candidates = MetadataStructureResolverService._placement_candidates(
            item, structure
        )
        placement = MetadataStructureResolverService._choose_candidate(
            item, structure["show"], candidates
        )
        self.assertEqual("season", placement["kind"])
        self.assertEqual(1, placement["season_number"])
        self.assertIsNone(placement["episode_number"])

    def test_separate_uzamaid_ova_maps_to_provider_season_zero_episode(self):
        item = {
            "provider": "anilist",
            "provider_item_id": "108548",
            "english_name": "UzaMaid!: My Maid Is Still Seriously Way Too Annoying...",
            "romaji_name": "Uchi no Maid ga Uzasugiru! OVA",
            "media_format": "OVA",
            "release_date": "2019-04-24",
            "episode_count": 1,
        }
        structure = self._uzamaid_structure()
        candidates = MetadataStructureResolverService._placement_candidates(
            item, structure
        )
        placement = MetadataStructureResolverService._choose_candidate(
            item, structure["show"], candidates
        )
        self.assertEqual("special_episode", placement["kind"])
        self.assertEqual(0, placement["season_number"])
        self.assertEqual(1, placement["episode_number"])

    def test_tv_format_does_not_enter_special_branch_before_scoring(self):
        item = {
            "provider": "anilist",
            "provider_item_id": "101506",
            "english_name": "UzaMaid!",
            "media_format": "TV",
            "release_date": "2018-10-05",
            "episode_count": 12,
        }
        candidates = MetadataStructureResolverService._placement_candidates(
            item, self._uzamaid_structure()
        )
        kinds = {candidate["kind"] for candidate in candidates}
        # Both kinds are considered; provider evidence chooses the winner.
        self.assertEqual({"season", "special_episode"}, kinds)


if __name__ == "__main__":
    unittest.main()
