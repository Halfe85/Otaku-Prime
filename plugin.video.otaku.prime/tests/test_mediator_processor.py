import unittest

from resources.lib.services.mediator_processor import MediatorProcessor
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)
from resources.lib.service_lifecycle import ServiceWorkHalted


class Endpoint:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def available(self, item):
        return item.get("simkl_id") is not None

    def resolve(self, item):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.result)


def placement():
    return {
        "provider_path": "simkl",
        "provider_id": "100",
        "provider_reference_id": None,
        "library_type": "series",
        "tv_show": {
            "name": "Old relation root",
            "romaji_name": "Old relation root",
            "simkl_id": "11",
            "anilist_id": "22",
            "mal_id": "33",
            "kitsu_id": "44",
            "source": "simkl_relation_root",
        },
        "structural_owner": {
            "name": "TVDB Owner",
            "simkl_id": "900",
            "tvdb_id": "74796",
            "source": "simkl_tvdb_crossmap_validated",
        },
        "season": {
            "number": 17,
            "number_source": "mapped_tvdb_seasons",
            "name": "Part",
            "first_episode": 1,
            "last_episode": 2,
            "structural_season_number": 17,
        },
        "episodes": [
            {"source_episode_number": 1, "season_number": 17, "episode_number": 1},
            {"source_episode_number": 2, "season_number": 17, "episode_number": 2},
        ],
        "relation_path": ["11", "100"],
    }


class MediatorProcessorTests(unittest.TestCase):
    def item(self):
        return {
            "local_id": "abcdef",
            "simkl_id": "100",
            "anilist_id": "22",
            "mal_id": "33",
            "kitsu_id": "44",
            "media_format": "TV",
            "episode_count": 2,
        }

    def test_production_processor_constructs_only_simkl_endpoint(self):
        processor = MediatorProcessor(network_timeout=3)
        self.assertEqual({"simkl"}, set(processor.endpoints))
        self.assertEqual(3, processor.endpoints["simkl"].client.timeout)

    def test_simkl_is_the_only_live_provider_path(self):
        simkl = Endpoint(result=placement())
        ignored = Endpoint(result=placement())
        processor = MediatorProcessor(endpoints={"simkl": simkl, "anilist": ignored})

        result = processor.resolve(self.item())

        self.assertEqual(1, simkl.calls)
        self.assertEqual(0, ignored.calls)
        self.assertEqual("simkl", result["provider_path"])

    def test_simkl_failure_is_terminal_and_keeps_reason(self):
        simkl = Endpoint(error=RuntimeError("HTTP 503"))
        ignored = Endpoint(result=placement())
        processor = MediatorProcessor(endpoints={"simkl": simkl, "anilist": ignored})

        with self.assertRaises(MediatorPlacementError) as caught:
            processor.resolve(self.item())

        self.assertIn("Simkl mediation failed", str(caught.exception))
        self.assertIn("HTTP 503", str(caught.exception))
        self.assertEqual(1, simkl.calls)
        self.assertEqual(0, ignored.calls)

    def test_missing_simkl_identity_is_terminal(self):
        item = self.item()
        item["simkl_id"] = None
        endpoint = Endpoint(result=placement())

        with self.assertRaises(MediatorPlacementError) as caught:
            MediatorProcessor(endpoints={"simkl": endpoint}).resolve(item)

        self.assertIn("no usable Simkl identity", str(caught.exception))
        self.assertEqual(0, endpoint.calls)

    def test_conflicted_exact_identity_is_not_bypassed(self):
        item = self.item()
        item["identity_resolution_status"] = "CONFLICT_EXACT"
        endpoint = Endpoint(result=placement())

        with self.assertRaises(MediatorPlacementError) as caught:
            MediatorProcessor(endpoints={"simkl": endpoint}).resolve(item)

        self.assertIn("conflicted", str(caught.exception))
        self.assertEqual(0, endpoint.calls)

    def test_incomplete_simkl_coverage_is_deferred_without_fallback(self):
        item = self.item()
        item["episode_count"] = 3
        endpoint = Endpoint(result=placement())

        with self.assertRaises(MediatorMetadataPending) as caught:
            MediatorProcessor(endpoints={"simkl": endpoint}).resolve(item)

        self.assertIn("covered 2 of 3", str(caught.exception))
        self.assertEqual("simkl", caught.exception.placement["provider_path"])

    def test_tv_series_requires_tvdb_structural_owner(self):
        value = placement()
        value["structural_owner"] = {"name": "Unknown", "tvdb_id": None}

        with self.assertRaises(MediatorPlacementError) as caught:
            MediatorProcessor(endpoints={"simkl": Endpoint(result=value)}).resolve(self.item())

        self.assertIn("TVDB structural series owner", str(caught.exception))

    def test_every_source_episode_requires_explicit_tvdb_coordinate(self):
        value = placement()
        value["episodes"][1]["episode_number"] = None

        with self.assertRaises(MediatorPlacementError) as caught:
            MediatorProcessor(endpoints={"simkl": Endpoint(result=value)}).resolve(self.item())

        self.assertIn("explicit TVDB coordinates", str(caught.exception))
        self.assertIn("2", str(caught.exception))

    def test_tvdb_owner_replaces_relation_root_as_catalogue_owner(self):
        result = MediatorProcessor(
            endpoints={"simkl": Endpoint(result=placement())}
        ).resolve(self.item())

        show = result["tv_show"]
        self.assertEqual("TVDB Owner", show["name"])
        self.assertEqual("74796", show["tvdb_id"])
        self.assertIsNone(show["simkl_id"])
        self.assertIsNone(show["anilist_id"])
        self.assertIsNone(show["mal_id"])
        self.assertIsNone(show["kitsu_id"])
        self.assertEqual("simkl_tvdb_structural_owner", show["source"])
        self.assertNotIn("relation_path", result)

    def test_trace_records_relation_path_before_it_is_discarded(self):
        with self.assertLogs("otaku_prime.services-mediator_trace", level="INFO") as logs:
            MediatorProcessor(
                endpoints={"simkl": Endpoint(result=placement())}
            ).resolve(self.item())

        combined = "\n".join(logs.output)
        self.assertIn("MEDIATOR[abcdef]", combined)
        self.assertIn("PLACEMENT_DISCOVERED", combined)
        self.assertIn("simkl_relation_path_observed", combined)
        self.assertIn("74796", combined)

    def test_shutdown_checkpoint_remains_terminal(self):
        halted = [False]

        class HaltingEndpoint(Endpoint):
            def resolve(self, item):
                self.calls += 1
                halted[0] = True
                return dict(self.result)

        endpoint = HaltingEndpoint(result=placement())
        with self.assertRaises(ServiceWorkHalted):
            MediatorProcessor(
                endpoints={"simkl": endpoint},
                halt_requested=lambda: halted[0],
            ).resolve(self.item())


if __name__ == "__main__":
    unittest.main()
