import datetime
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.database.metadata_provider import MetadataProviderStore
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.services.metadata_resolver import (
    MetadataResolverService,
    TMDBMetadataClient,
    TVDBMetadataClient,
    _title_variants,
)
from resources.lib.services.watchlist_sync import WatchlistSyncService
from resources.lib.users import UserStore


def stamp(year, month, day):
    return int(datetime.datetime(
        year, month, day, tzinfo=datetime.timezone.utc
    ).timestamp())


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeMetadataClient:
    def test_connection(self):
        return {"provider": "tmdb", "ok": True}

    def search_series(self, title, year=None):
        return [{
            "id": 100,
            "name": "Franchise",
            "original_name": "Franchise",
            "year": 2020,
        }]

    def get_show(self, show_id):
        self._check_show(show_id)
        return {
            "id": 100,
            "name": "Franchise",
            "original_name": "Franchise",
            "year": 2020,
            "seasons": [
                {"id": 900, "number": 0, "name": "Specials", "air_date": "2020-06-01"},
                {"id": 902, "number": 2, "name": "The Northern War", "air_date": "2021-01-10"},
            ],
        }

    def get_season(self, show_id, season_number, season_id=None):
        self._check_show(show_id)
        if int(season_number) == 0:
            return {
                "id": 900,
                "number": 0,
                "name": "Specials",
                "air_date": "2020-06-01",
                "episodes": [
                    {"id": 1001, "number": 1, "name": "Recap", "air_date": "2020-03-01"},
                    {"id": 1007, "number": 7, "name": "Bonus Story", "air_date": "2020-06-01"},
                ],
            }
        if int(season_number) == 2:
            return {
                "id": 902,
                "number": 2,
                "name": "The Northern War",
                "air_date": "2021-01-10",
                "episodes": [
                    {"id": 2001, "number": 1, "name": "Arrival", "air_date": "2021-01-10"},
                    {"id": 2002, "number": 2, "name": "Battle", "air_date": "2021-01-17"},
                ],
            }
        raise AssertionError("unexpected season {}".format(season_number))

    @staticmethod
    def _check_show(show_id):
        if int(show_id) != 100:
            raise AssertionError("unexpected show {}".format(show_id))


class MetadataAuthenticationTests(unittest.TestCase):
    def test_tmdb_bearer_connection_uses_authorization_header(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"images": {}})

        TMDBMetadataClient(
            "bearer", "read-token", opener=opener
        ).test_connection()

        self.assertEqual(1, len(requests))
        self.assertEqual("Bearer read-token", requests[0].get_header("Authorization"))
        self.assertNotIn("api_key", parse_qs(urlsplit(requests[0].full_url).query))

    def test_tmdb_v3_api_key_connection_uses_query_parameter(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"images": {}})

        TMDBMetadataClient(
            "api_key", "v3-key", opener=opener
        ).test_connection()

        query = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(["v3-key"], query["api_key"])
        self.assertIsNone(requests[0].get_header("Authorization"))

    def test_tvdb_login_posts_project_key_and_optional_subscriber_pin(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return Response({"data": {"token": "tvdb-token"}})

        client = TVDBMetadataClient(
            "project-key", "subscriber-pin", opener=opener
        )
        self.assertEqual("tvdb-token", client.login())

        body = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual("project-key", body["apikey"])
        self.assertEqual("subscriber-pin", body["pin"])
        self.assertTrue(requests[0].full_url.endswith("/v4/login"))

    def test_tvdb_search_retains_aliases_for_non_latin_primary_title(self):
        def opener(request, timeout):
            return Response({"data":[{"tvdb_id":"423688","name":"龙族",
              "aliases":["Long Zu","Dragon Raja -The Blazing Dawn-"],
              "first_air_time":"2022-08-19"}]})
        client=TVDBMetadataClient("key",bearer_token="token",
          bearer_expires_at=9999999999,opener=opener)
        result=client.search_series("Dragon Raja -The Blazing Dawn-",2022)[0]
        self.assertEqual(["Long Zu","Dragon Raja -The Blazing Dawn-"],result["aliases"])

    def test_tvdb_search_does_not_treat_overview_as_title_alias(self):
        def opener(request, timeout):
            return Response({"data":[{"tvdb_id":"1","name":"本題",
              "aliases":["Real Alias"],"overviews":{"eng":"A long plot description"},
              "first_air_time":"2024-01-01"}]})
        client=TVDBMetadataClient("key",bearer_token="token",
          bearer_expires_at=9999999999,opener=opener)
        result=client.search_series("Real Alias",2024)[0]
        self.assertEqual(["Real Alias"],result["aliases"])

    def test_title_variants_remove_numbered_season_and_part_suffixes(self):
        self.assertEqual(
          ["That Time I Got Reincarnated as a Slime Season 2 Part 2",
           "That Time I Got Reincarnated as a Slime Season 2",
           "That Time I Got Reincarnated as a Slime"],
          _title_variants("That Time I Got Reincarnated as a Slime Season 2 Part 2"))
        self.assertIn("Tensei Shitara Slime Datta Ken",
          _title_variants("Tensei Shitara Slime Datta Ken 2nd Season Part 2"))

    def test_show_matching_uses_aliases(self):
        match=MetadataResolverService._best_show(
          ["Dragon Raja -The Blazing Dawn-","Long Zu"],2022,
          [{"id":"423688","name":"龙族","original_name":"龙族","year":2022,
            "aliases":["Long Zu","Dragon Raja -The Blazing Dawn-"]}])
        self.assertEqual("423688",match["id"])

    def test_show_resolution_searches_base_title_when_franchise_is_a_season(self):
        class Client:
            queries = []
            def search_series(self, title, year=None):
                self.queries.append(title)
                if title == "That Time I Got Reincarnated as a Slime":
                    return [{"id":"295068","name":title,"year":2018}]
                return []
            def get_show(self, show_id):
                return {"id":show_id,"name":"Slime","seasons":[]}

        resolver=MetadataResolverService.__new__(MetadataResolverService)
        resolver._show_cache={}
        resolver.status=lambda: {"provider":"thetvdb"}
        client=Client()
        show=resolver._resolve_show(client,{
          "related_series_id":"series-1",
          "franchise_english_name":"That Time I Got Reincarnated as a Slime Season 2",
          "franchise_romaji_name":"Tensei Shitara Slime Datta Ken 2nd Season",
          "franchise_release_date":"2021-01-12"})
        self.assertEqual("295068",show["id"])
        self.assertIn("That Time I Got Reincarnated as a Slime",client.queries)

    def test_main_series_ona_is_numbered_but_side_story_ona_is_special(self):
        resolver=MetadataResolverService.__new__(MetadataResolverService)
        self.assertFalse(resolver._is_special(
          {"media_category":"ona","relation_type":None}))
        self.assertTrue(resolver._is_special(
          {"media_category":"ona","relation_type":"SIDE_STORY"}))


class MetadataResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "users.sqlite")
        UserStore(self.db_path).initialize()
        self.media = WatchlistMediaStore(self.db_path)
        self.media.initialize()
        self.config = MetadataProviderStore(self.db_path)
        self.config.initialize()
        self.config.save_tmdb("api_key", "unit-test-key", verified_at=1)
        self.resolver = MetadataResolverService(
            self.config,
            client_factory=lambda provider, config: FakeMetadataClient(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _franchise(self):
        return self.media.upsert_tv_series(
            english_name="Franchise",
            romaji_name="Franchise",
            anilist_root_id=1,
            franchise_resolved=True,
        )

    def _row(self, table, local_id):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute(
                "SELECT * FROM {} WHERE local_id=?".format(table),
                (local_id,),
            ).fetchone())

    def test_resolves_franchise_named_season_and_normal_episodes(self):
        series = self._franchise()
        season = self.media.upsert_season(
            series,
            2,
            english_name="Completely Different Arc Title",
            anilist_id=2,
            release_date="2021-01-10",
            media_category="tv",
            kodi_season_number=2,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", season, "anilist", "CURRENT")
        episode1 = self.media.upsert_episode(season, 1)
        episode2 = self.media.upsert_episode(season, 2)
        self.media.schedule_release("episode", episode1, stamp(2021, 1, 10))
        self.media.schedule_release("episode", episode2, stamp(2021, 1, 17))

        result = self.resolver.run_once()

        self.assertEqual(1, result["resolved"])
        self.assertEqual(0, result["unresolved"])
        season_row = self._row("seasons", season)
        self.assertEqual("tmdb", season_row["metadata_provider"])
        self.assertEqual("902", season_row["metadata_season_id"])
        self.assertEqual(2, season_row["kodi_season_number"])
        self.assertEqual("The Northern War", season_row["kodi_season_name"])
        self.assertEqual(1, season_row["kodi_resolved"])
        self.assertEqual(1, self._row("episodes", episode1)["kodi_episode_number"])
        self.assertEqual("Arrival", self._row("episodes", episode1)["kodi_episode_name"])
        self.assertEqual(2, self._row("episodes", episode2)["kodi_episode_number"])

        series_row = self._row("tv_series", series)
        self.assertEqual("tmdb", series_row["metadata_provider"])
        self.assertEqual("100", series_row["metadata_show_id"])
        self.assertEqual("Franchise", series_row["metadata_show_name"])
        self.assertEqual(2020, series_row["metadata_show_year"])

    def test_special_uses_provider_owned_s00_number_not_local_episode_number(self):
        series = self._franchise()
        special = self.media.upsert_season(
            series,
            3,
            english_name="Bonus Story",
            anilist_id=30,
            release_date="2020-06-01",
            media_category="ova",
            kodi_season_number=0,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", special, "anilist", "COMPLETED")
        episode = self.media.upsert_episode(special, 1)
        self.media.schedule_release("episode", episode, stamp(2020, 6, 1))

        result = self.resolver.run_once()

        self.assertEqual(1, result["resolved"])
        special_row = self._row("seasons", special)
        episode_row = self._row("episodes", episode)
        self.assertEqual(0, special_row["kodi_season_number"])
        self.assertEqual("Specials", special_row["kodi_season_name"])
        self.assertEqual(1, special_row["kodi_resolved"])
        self.assertEqual(7, episode_row["kodi_episode_number"])
        self.assertEqual("1007", episode_row["metadata_episode_id"])
        self.assertEqual("Bonus Story", episode_row["kodi_episode_name"])

    def test_ambiguous_special_is_left_unresolved_instead_of_becoming_s00e01(self):
        series = self._franchise()
        special = self.media.upsert_season(
            series,
            3,
            english_name="Unknown OVA",
            anilist_id=31,
            media_category="ova",
            kodi_season_number=0,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", special, "anilist", "PLANNING")
        episode = self.media.upsert_episode(special, 1)

        result = self.resolver.run_once()

        self.assertEqual(0, result["resolved"])
        self.assertEqual(1, result["unresolved"])
        self.assertEqual(0, self._row("seasons", special)["kodi_resolved"])
        self.assertIsNone(self._row("episodes", episode)["kodi_episode_number"])

    def test_watchlist_fetch_is_blocked_until_metadata_provider_is_configured(self):
        class Gate:
            def is_configured(self):
                return False
            def status(self):
                return {"configured": False, "provider": None}

        class Importer:
            calls = 0
            def sync(self):
                self.calls += 1
                return {"imported": 1}

        importer = Importer()
        result = WatchlistSyncService([importer], gate=Gate()).run_once()

        self.assertEqual(0, importer.calls)
        self.assertEqual("metadata_provider_required", result[0]["blocked"])


if __name__ == "__main__":
    unittest.main()
