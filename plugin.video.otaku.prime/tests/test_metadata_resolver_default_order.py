import json
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.services.metadata_resolver_default_order import (
    MetadataResolverService,
    TVDBDefaultOrderMetadataClient,
)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TVDBDefaultOrderTests(unittest.TestCase):
    def test_show_seasons_are_derived_from_default_episode_dates(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if "/series/123/extended" in request.full_url:
                return Response({"data": {
                    "id": 123,
                    "name": "Example",
                    "firstAired": "2018-01-01",
                    "defaultSeasonType": 1,
                    "seasons": [
                        {"id": 501, "number": 1,
                         "type": {"id": 1, "name": "Aired Order"}},
                        {"id": 502, "number": 2,
                         "type": {"id": 1, "name": "Aired Order"}},
                    ],
                }})
            if "/series/123/episodes/default" in request.full_url:
                return Response({
                    "data": {"episodes": [
                        {"id": 1001, "seasonNumber": 1, "number": 1,
                         "name": "First", "aired": "2018-01-08"},
                        {"id": 2001, "seasonNumber": 2, "number": 1,
                         "name": "Second", "aired": "2019-07-07"},
                    ]},
                    "links": {"next": None},
                })
            raise AssertionError(request.full_url)

        client = TVDBDefaultOrderMetadataClient(
            "key", bearer_token="token", bearer_expires_at=9999999999,
            opener=opener,
        )
        show = client.get_show(123)
        self.assertEqual(2, len(show["seasons"]))
        self.assertEqual("2018-01-08", show["seasons"][0]["air_date"])
        self.assertEqual("2019-07-07", show["seasons"][1]["air_date"])
        self.assertEqual(501, show["seasons"][0]["id"])
        self.assertEqual(502, show["seasons"][1]["id"])

    def test_staged_second_season_matches_provider_default_order_date(self):
        candidates = [
            {"id": 501, "number": 1, "air_date": "2018-01-08"},
            {"id": 502, "number": 2, "air_date": "2019-07-07"},
        ]
        match = MetadataResolverService._best_staged_season(
            {"release_date": "2019-07-07"}, candidates
        )
        self.assertEqual(2, match["number"])

    def test_single_provider_season_is_safe_fallback_after_show_match(self):
        match = MetadataResolverService._best_staged_season(
            {"release_date": "2020-01-01"},
            [{"id": 501, "number": 1, "air_date": None}],
        )
        self.assertEqual(1, match["number"])

    def test_tvdb_search_uses_year_then_falls_back_without_year(self):
        queries = []

        def opener(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            queries.append(query)
            if "year" in query:
                return Response({"data": []})
            return Response({"data": [{
                "tvdb_id": "123", "name": "Example", "year": "2020"
            }]})

        client = TVDBDefaultOrderMetadataClient(
            "key", bearer_token="token", bearer_expires_at=9999999999,
            opener=opener,
        )
        results = client.search_series("Example", 2020)
        self.assertEqual("123", results[0]["id"])
        self.assertEqual(["2020"], queries[0]["year"])
        self.assertNotIn("year", queries[1])


if __name__ == "__main__":
    unittest.main()
